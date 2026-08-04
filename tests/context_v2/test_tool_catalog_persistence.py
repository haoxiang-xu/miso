from __future__ import annotations

import hashlib
import json

import pytest

from unchain.context.tool_catalog import ToolCatalogEnvelope
from unchain.journal.models import AttemptRef, GenerationRef, OperationRef
from unchain.journal.tool_catalog_persistence import (
    ToolCatalogPersistenceError,
    persist_tool_catalog_snapshot,
)
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


ATTEMPT = AttemptRef(
    GenerationRef("execution-catalog-persist", "generation-catalog-persist"),
    "attempt-catalog-persist",
)


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _catalog() -> ToolCatalogEnvelope:
    return ToolCatalogEnvelope(
        attempt=ATTEMPT,
        iteration=3,
        provider="openai",
        model="frontier-model",
        semantic_schemas=[],
        entries=[],
        required_betas_sha256=_sha256_json([]),
        prompt_sha256="0" * 64,
        exposure_plan_sha256="1" * 64,
    )


def _operation(name: str, digest: str) -> OperationRef:
    return OperationRef(name, digest)


def test_tool_catalog_persists_bytes_receipt_and_restart_snapshot(tmp_path) -> None:
    store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    catalog = _catalog()

    snapshot = persist_tool_catalog_snapshot(
        journal=repository,
        artifacts=repository,
        envelope=catalog,
        artifact_operation=_operation("catalog-artifact", "2" * 64),
        event_operation=_operation("catalog-event", "3" * 64),
        event_id="event-catalog-snapshot",
    )

    reopened = SQLiteContextV2Store(
        database_path=store.database_path,
        object_directory=store.object_directory,
    ).bind_execution(ATTEMPT.generation.execution_id)
    lookup = reopened.lookup_tool_catalog_receipts(
        attempt=ATTEMPT,
        iteration=3,
    )

    assert reopened.read_full_verified(artifact=snapshot.artifact) == (
        catalog.canonical_bytes()
    )
    assert len(lookup.events) == 1
    assert lookup.events[0].event_id == "event-catalog-snapshot"
    assert lookup.events[0].payload == {
        "iteration": 3,
        "catalog_sha256": catalog.catalog_sha256,
        "catalog_artifact": snapshot.artifact.to_dict(),
    }
    assert lookup.events[0].resource_refs == (snapshot.artifact.ref,)
    assert snapshot.envelope == catalog
    assert snapshot.event_cursor.store_seq == lookup.events[0].store_seq


def test_tool_catalog_persistence_is_idempotent_for_same_operations(tmp_path) -> None:
    store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    catalog = _catalog()
    arguments = {
        "journal": repository,
        "artifacts": repository,
        "envelope": catalog,
        "artifact_operation": _operation("catalog-artifact", "2" * 64),
        "event_operation": _operation("catalog-event", "3" * 64),
        "event_id": "event-catalog-snapshot",
    }

    first = persist_tool_catalog_snapshot(**arguments)
    reopened = SQLiteContextV2Store(
        database_path=store.database_path,
        object_directory=store.object_directory,
    ).bind_execution(ATTEMPT.generation.execution_id)
    second = persist_tool_catalog_snapshot(
        **{
            **arguments,
            "journal": reopened,
            "artifacts": reopened,
        }
    )

    assert second == first
    assert (
        len(
            repository.lookup_tool_catalog_receipts(
                attempt=ATTEMPT,
                iteration=3,
            ).events
        )
        == 1
    )


def test_tool_catalog_readback_failure_never_appends_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    store = SQLiteContextV2Store(
        database_path=tmp_path / "memory_v2" / "context_v2.sqlite3",
        object_directory=tmp_path / "memory_v2" / "objects",
    )
    repository = store.bind_execution(ATTEMPT.generation.execution_id)
    monkeypatch.setattr(
        repository,
        "read_full_verified",
        lambda **_kwargs: b"changed-after-put",
    )

    with pytest.raises(ToolCatalogPersistenceError, match="readback"):
        persist_tool_catalog_snapshot(
            journal=repository,
            artifacts=repository,
            envelope=_catalog(),
            artifact_operation=_operation("catalog-artifact", "2" * 64),
            event_operation=_operation("catalog-event", "3" * 64),
            event_id="event-catalog-snapshot",
        )

    assert not repository.lookup_tool_catalog_receipts(
        attempt=ATTEMPT,
        iteration=3,
    ).events
