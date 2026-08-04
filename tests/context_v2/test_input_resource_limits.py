from __future__ import annotations

from dataclasses import dataclass

import pytest

import unchain.context.models as context_models
import unchain.journal.runtime as journal_runtime
import unchain.journal.snapshot as journal_snapshot
from unchain.context.models import ContextCompileRequest
from unchain.journal import (
    AttemptRef,
    BoundaryResourceLimitError,
    GenerationRef,
    JournalEvent,
    OperationRef,
    ResourceRef,
    capture_journal_snapshot,
    journal_event_to_semantic_event,
)


@dataclass(frozen=True)
class _TinyLimits:
    max_items: int = 10_000
    max_bytes: int = 32 * 1024 * 1024
    max_depth: int = 64
    max_nodes: int = 1_000_000


class _ExplodingResourceRef(ResourceRef):
    serialization_calls = 0

    def to_dict(self) -> dict[str, object]:
        type(self).serialization_calls += 1
        if type(self).serialization_calls > 4:
            raise AssertionError("resource refs were materialized before validation")
        return super().to_dict()


def _assert_limit_error(
    error: BoundaryResourceLimitError,
    *,
    boundary: str,
    dimension: str,
    limit: int,
) -> None:
    assert error.boundary == boundary
    assert error.dimension == dimension
    assert error.limit == limit
    assert error.observed > limit


def _nested_payload(depth: int) -> dict[str, object]:
    root: dict[str, object] = {}
    current = root
    for _ in range(depth):
        child: dict[str, object] = {}
        current["child"] = child
        current = child
    return root


def _event(*, payload: dict[str, object] | None = None) -> JournalEvent:
    return JournalEvent(
        event_id="event-1",
        event_type="message.user",
        attempt=AttemptRef(
            GenerationRef("execution-1", "generation-1"),
            "attempt-1",
        ),
        operation=OperationRef("operation-1", "a" * 64),
        store_seq=1,
        payload=payload or {"message": {"role": "user", "content": "current"}},
    )


def test_compile_request_rejects_excess_source_messages_without_truncation() -> None:
    with pytest.raises(BoundaryResourceLimitError) as caught:
        ContextCompileRequest(
            case="too-many-messages",
            source_messages=tuple(
                {"role": "user", "content": "x"} for _ in range(10_001)
            ),
        )

    _assert_limit_error(
        caught.value,
        boundary="context_compile_request.source_messages",
        dimension="items",
        limit=10_000,
    )


def test_compile_request_rejects_aggregate_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        context_models,
        "CONTEXT_COMPILE_REQUEST_LIMITS",
        _TinyLimits(max_bytes=128),
        raising=False,
    )

    with pytest.raises(BoundaryResourceLimitError) as caught:
        ContextCompileRequest(
            case="too-many-bytes",
            source_messages=({"role": "user", "content": "x" * 256},),
        )

    _assert_limit_error(
        caught.value,
        boundary="context_compile_request",
        dimension="bytes",
        limit=128,
    )


def test_compile_request_rejects_deep_json_before_python_recursion_failure() -> None:
    with pytest.raises(BoundaryResourceLimitError) as caught:
        ContextCompileRequest(
            case="too-deep",
            source_messages=({"role": "user", "content": "current"},),
            semantic_events=({"payload": _nested_payload(65)},),
        )

    _assert_limit_error(
        caught.value,
        boundary="context_compile_request",
        dimension="depth",
        limit=64,
    )


def test_compile_request_invalid_deep_top_level_value_is_still_typed() -> None:
    nested: object = []
    for _ in range(1_100):
        nested = [nested]

    with pytest.raises(BoundaryResourceLimitError) as caught:
        ContextCompileRequest(
            case="invalid-deep-top-level",
            source_messages=(nested,),
        )

    _assert_limit_error(
        caught.value,
        boundary="context_compile_request",
        dimension="depth",
        limit=64,
    )


def test_compile_request_rejects_total_json_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        context_models,
        "CONTEXT_COMPILE_REQUEST_LIMITS",
        _TinyLimits(max_nodes=12),
        raising=False,
    )

    with pytest.raises(BoundaryResourceLimitError) as caught:
        ContextCompileRequest(
            case="too-many-nodes",
            source_messages=({"role": "user", "content": list(range(20))},),
        )

    _assert_limit_error(
        caught.value,
        boundary="context_compile_request",
        dimension="nodes",
        limit=12,
    )


def test_snapshot_rejects_event_count_before_decoding_entries() -> None:
    raw = {
        "schema": "unchain.journal_snapshot.v1",
        "execution_id": "execution-1",
        "high_water": None,
        "events": [None] * 10_001,
        "snapshot_sha256": "a" * 64,
        "event_count": 10_001,
    }

    with pytest.raises(BoundaryResourceLimitError) as caught:
        journal_snapshot.JournalSnapshot.from_dict(raw)

    _assert_limit_error(
        caught.value,
        boundary="journal_snapshot.events",
        dimension="items",
        limit=10_000,
    )


def test_snapshot_rejects_aggregate_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        journal_snapshot,
        "JOURNAL_SNAPSHOT_LIMITS",
        _TinyLimits(max_bytes=128),
        raising=False,
    )

    with pytest.raises(BoundaryResourceLimitError) as caught:
        capture_journal_snapshot(
            execution_id="execution-1",
            events=(_event(payload={"content": "x" * 256}),),
        )

    _assert_limit_error(
        caught.value,
        boundary="journal_snapshot",
        dimension="bytes",
        limit=128,
    )


def test_snapshot_bounds_shape_before_materializing_all_resource_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        journal_snapshot,
        "JOURNAL_SNAPSHOT_LIMITS",
        _TinyLimits(max_nodes=16),
        raising=False,
    )
    _ExplodingResourceRef.serialization_calls = 0
    resource = _ExplodingResourceRef("artifact", "artifact-1", 1)
    event = JournalEvent(
        event_id="event-1",
        event_type="message.user",
        attempt=AttemptRef(
            GenerationRef("execution-1", "generation-1"),
            "attempt-1",
        ),
        operation=OperationRef("operation-1", "a" * 64),
        store_seq=1,
        payload={"content": "current"},
        resource_refs=(resource,) * 100,
    )

    with pytest.raises(BoundaryResourceLimitError) as caught:
        capture_journal_snapshot(
            execution_id="execution-1",
            events=(event,),
        )

    _assert_limit_error(
        caught.value,
        boundary="journal_snapshot",
        dimension="nodes",
        limit=16,
    )
    assert _ExplodingResourceRef.serialization_calls <= 4


def test_semantic_projection_rejects_deep_event_payload() -> None:
    event = _event(payload={"payload": _nested_payload(65)})

    with pytest.raises(BoundaryResourceLimitError) as caught:
        journal_event_to_semantic_event(event)

    _assert_limit_error(
        caught.value,
        boundary="semantic_event_projection",
        dimension="depth",
        limit=64,
    )


def test_semantic_projection_rejects_aggregate_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        journal_runtime,
        "SEMANTIC_EVENT_PROJECTION_LIMITS",
        _TinyLimits(max_bytes=128),
        raising=False,
    )
    event = _event(payload={"content": "x" * 256})

    with pytest.raises(BoundaryResourceLimitError) as caught:
        journal_event_to_semantic_event(event)

    _assert_limit_error(
        caught.value,
        boundary="semantic_event_projection",
        dimension="bytes",
        limit=128,
    )
