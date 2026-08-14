from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from unchain.context import (
    ContextBudget,
    ContextBuildEnvelope,
    ContextBuildStatus,
    ContextCompileRequest,
    HandoffEnvelope,
    HandoffStatus,
    OpaqueSecretHandle,
    PinnedTaskState,
    SourceMessageCursor,
)
from unchain.journal import (
    ArtifactRef,
    AttemptRef,
    EventCursor,
    EventRange,
    GenerationRef,
    JournalAppendResult,
    JournalAppendRequest,
    JournalEvent,
    JournalPage,
    OperationRef,
    ResourceRef,
)
from unchain.memory.workspace import (
    CandidateStatus,
    ConsolidationJob,
    EntryRevision,
    JobStatus,
    MemoryCandidate,
    MemoryEntry,
    MemoryEntryPage,
    MemoryEntryKind,
    MemoryLink,
    MemorySpace,
    PromotionProposal,
    PromotionStatus,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "pupu_p0"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _resource(identifier: str = "resource-1", revision: int = 1) -> ResourceRef:
    return ResourceRef(kind="artifact", resource_id=identifier, revision=revision)


def test_journal_records_are_versioned_immutable_and_round_trip() -> None:
    resource = _resource()
    artifact = ArtifactRef(
        ref=resource,
        media_type="application/json",
        byte_length=12,
        sha256=SHA_A,
        preview="synthetic",
    )
    start = EventCursor(store_seq=1, event_id="event-1")
    end = EventCursor(store_seq=2, event_id="event-2")
    event_range = EventRange(start=start, end=end)
    generation = GenerationRef(execution_id="execution-1", generation_id="generation-1")
    attempt = AttemptRef(generation=generation, attempt_id="attempt-1")
    operation = OperationRef(operation_id="operation-1", payload_sha256=SHA_B)
    event = JournalEvent(
        event_id="event-2",
        event_type="tool.result",
        attempt=attempt,
        operation=operation,
        store_seq=2,
        payload={"nested": [{"answer": 42}]},
        resource_refs=(resource,),
    )
    append_request = JournalAppendRequest(
        event_id="event-2",
        event_type="tool.result",
        attempt=attempt,
        operation=operation,
        payload={"nested": [{"answer": 42}]},
        resource_refs=(resource,),
    )
    append_result = JournalAppendResult(event=event, cursor=end, duplicate=False)
    page = JournalPage(events=(event,), next_cursor=end, has_more=False)

    records = (
        resource,
        artifact,
        start,
        event_range,
        generation,
        attempt,
        operation,
        append_request,
        event,
        append_result,
        page,
    )
    for record in records:
        restored = type(record).from_dict(record.to_dict())
        assert restored == record
        assert restored.to_dict()["schema"].startswith("unchain.")

    with pytest.raises(TypeError):
        event.payload["nested"] = []
    with pytest.raises(TypeError):
        event.payload["nested"][0]["answer"] = 7
    with pytest.raises(FrozenInstanceError):
        event.store_seq = 3
    detached = event.to_dict()
    detached["payload"]["nested"][0]["answer"] = 99
    assert event.to_dict()["payload"]["nested"][0]["answer"] == 42


def test_context_records_round_trip_and_fixture_inputs_parse() -> None:
    budget = ContextBudget(
        context_window_tokens=100_000,
        output_reserve_tokens=8_000,
        transport_margin_tokens=2_000,
        available_input_tokens=90_000,
        pressure_threshold_tokens=81_000,
    )
    source_range = EventRange(
        start=EventCursor(store_seq=1, event_id="event-1"),
        end=EventCursor(store_seq=8, event_id="event-8"),
    )
    envelope = ContextBuildEnvelope(
        build_id="build-1",
        execution_id="execution-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
        provider="synthetic",
        model="synthetic-model",
        budget=budget,
        source_range=source_range,
        included_ranges=(source_range,),
        transformed_ranges=(),
        checkpoint_refs=(_resource("checkpoint-1"),),
        artifact_refs=(_resource("artifact-2"),),
        estimated_input_tokens=2_400,
        status=ContextBuildStatus.COMPLETE,
    )
    handoff = HandoffEnvelope(
        child_run_id="child-run-1",
        child_attempt=AttemptRef(
            generation=GenerationRef(
                execution_id="child-execution-1",
                generation_id="child-generation-1",
            ),
            attempt_id="child-run-1",
        ),
        status=HandoffStatus.COMPLETE,
        summary="Synthetic handoff",
        full_output_ref=_resource("child-output"),
        artifact_refs=(_resource("child-artifact"),),
        source_event_range=source_range,
        byte_length=128,
        sha256=SHA_A,
    )
    task_state = PinnedTaskState(
        state_id="task-state-1",
        revision=3,
        objective="Finish the synthetic task",
        success_criteria=("All checks pass",),
        constraints=("No product-specific URIs",),
        confirmed_decisions=("Use structured references",),
        open_questions=("None",),
        active_plan=("Build models", "Build ports"),
        artifact_refs=(_resource("artifact-3"),),
        memory_refs=(ResourceRef("memory", "entry-1", 2),),
        source_event_refs=(ResourceRef("event", "event-1", 1),),
        status="in_progress",
    )
    secret = OpaqueSecretHandle(handle_id="secret-handle-1", label="Login", scope="session")

    for record in (budget, envelope, handoff, task_state, secret):
        assert type(record).from_dict(record.to_dict()) == record

    assert handoff.child_attempt.generation.execution_id == "child-execution-1"


def test_handoff_rejects_child_run_identity_mismatch() -> None:
    source_range = EventRange(
        start=EventCursor(store_seq=1, event_id="event-1"),
        end=EventCursor(store_seq=2, event_id="event-2"),
    )

    with pytest.raises(ValueError, match="child_run_id"):
        HandoffEnvelope(
            child_run_id="child-run-1",
            child_attempt=AttemptRef(
                generation=GenerationRef(
                    execution_id="child-execution-1",
                    generation_id="child-generation-1",
                ),
                attempt_id="different-child-run",
            ),
            status=HandoffStatus.COMPLETE,
            summary="Synthetic handoff",
            full_output_ref=_resource("child-output"),
            artifact_refs=(),
            source_event_range=source_range,
            byte_length=128,
            sha256=SHA_A,
        )

    for fixture_path in sorted(FIXTURE_ROOT.glob("*.json")):
        if fixture_path.name == "manifest.json":
            continue
        fixture_input = json.loads(fixture_path.read_text(encoding="utf-8"))["input"]
        request = ContextCompileRequest.from_dict(fixture_input)
        assert request.to_dict() == fixture_input

    assert set(ContextBuildStatus) == {
        ContextBuildStatus.COMPLETE,
        ContextBuildStatus.PARTIAL,
        ContextBuildStatus.LEGACY,
        ContextBuildStatus.UNAVAILABLE,
    }


def test_compile_request_preserves_explicit_empty_semantic_events() -> None:
    raw = {
        "schema": ContextCompileRequest.SCHEMA,
        "case": "empty-events",
        "source_messages": [{"role": "user", "content": "current"}],
        "semantic_events": [],
    }

    assert ContextCompileRequest.from_dict(raw).to_dict() == raw


@pytest.mark.parametrize("event_id", [" event-1 ", "event/1", ""])
def test_compile_request_rejects_invalid_source_event_ids(event_id: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        ContextCompileRequest(
            case="invalid-source",
            source_messages=(),
            source_event_ids=(event_id,),
            source_event_store_seqs=(1,),
        )


def test_compile_request_requires_aligned_source_event_provenance() -> None:
    with pytest.raises(ValueError, match="aligned"):
        ContextCompileRequest(
            case="invalid-source",
            source_messages=(),
            source_event_ids=("event-1",),
            source_event_store_seqs=(),
        )

    with pytest.raises(ValueError, match="source message"):
        ContextCompileRequest(
            case="invalid-source",
            source_messages=(
                {"role": "user", "content": "old"},
                {"role": "user", "content": "current"},
            ),
            source_event_ids=("event-1",),
            source_event_store_seqs=(1,),
        )


def test_compile_request_accepts_explicit_partial_message_cursor_mapping() -> None:
    request = ContextCompileRequest(
        case="explicit-source-mapping",
        source_messages=(
            {"role": "system", "content": "current instructions"},
            {"role": "user", "content": "historical event"},
            {"role": "user", "content": "native current input"},
        ),
        source_message_cursors=(
            SourceMessageCursor(
                message_index=1,
                event_id="event-1",
                store_seq=4,
            ),
        ),
    )

    restored = ContextCompileRequest.from_dict(request.to_dict())

    assert restored == request
    assert restored.source_message_cursors[0].message_index == 1


def test_compile_request_rejects_ambiguous_legacy_and_explicit_provenance() -> None:
    with pytest.raises(ValueError, match="provenance"):
        ContextCompileRequest(
            case="ambiguous-source-mapping",
            source_messages=({"role": "user", "content": "history"},),
            source_event_ids=("event-1",),
            source_event_store_seqs=(1,),
            source_message_cursors=(
                SourceMessageCursor(0, "event-1", 1),
            ),
        )


def test_compile_request_requires_budget_with_build_identity() -> None:
    with pytest.raises(ValueError, match="budget"):
        ContextCompileRequest(
            case="missing-budget",
            source_messages=(),
            provider="openai",
            model="synthetic",
            build_id="build-1",
            execution_id="execution-1",
            generation_id="generation-1",
            attempt_id="attempt-1",
        )


def test_compile_request_rejects_a_non_checkpoint_checkpoint_ref() -> None:
    with pytest.raises(ValueError, match="checkpoint_ref"):
        ContextCompileRequest(
            case="wrong-ref",
            source_messages=(),
            checkpoint_ref=ResourceRef("artifact", "artifact-1", 1),
        )


def test_compile_request_requires_checkpoint_ref_request_binding() -> None:
    with pytest.raises(ValueError, match="supplied together"):
        ContextCompileRequest(
            case="unbound-checkpoint",
            source_messages=({"role": "user", "content": "current"},),
            checkpoint_ref=ResourceRef("checkpoint", "checkpoint-1", 1),
        )


def test_workspace_records_round_trip() -> None:
    content_ref = _resource("content-1")
    source_ref = ResourceRef("event", "event-1", 1)
    space = MemorySpace(
        space_id="space-1",
        namespace="agent:research",
        name="Research",
        description="Synthetic workspace",
        revision=1,
    )
    entry = MemoryEntry(
        entry_id="entry-1",
        space_id=space.space_id,
        path="/notes/decision.md",
        name="Decision",
        description="Why the synthetic decision was made",
        kind=MemoryEntryKind.MARKDOWN,
        revision=2,
        content_ref=content_ref,
        source_refs=(source_ref,),
        tags=("decision",),
        media_type="text/markdown",
    )
    revision = EntryRevision(
        entry_id=entry.entry_id,
        revision=2,
        content_ref=content_ref,
        content_sha256=SHA_A,
        byte_length=128,
        source_refs=(source_ref,),
        operation_id="operation-1",
    )
    link = MemoryLink(
        link_id="link-1",
        source_entry_ref=ResourceRef("memory", entry.entry_id, entry.revision),
        target_ref=ResourceRef("memory", "entry-2", 1),
        relation="supports",
        revision=1,
    )
    candidate = MemoryCandidate(
        candidate_id="candidate-1",
        path="/notes/candidate.md",
        name="Candidate",
        description="Synthetic candidate",
        kind=MemoryEntryKind.MARKDOWN,
        content_ref=content_ref,
        source_refs=(source_ref,),
        status=CandidateStatus.PENDING,
        revision=1,
        reason="User requested retention",
    )
    job = ConsolidationJob(
        job_id="job-1",
        candidate_refs=(ResourceRef("candidate", "candidate-1", 1),),
        status=JobStatus.PENDING,
        revision=1,
        operation_id="operation-2",
    )
    proposal = PromotionProposal(
        proposal_id="proposal-1",
        source_entry_ref=ResourceRef("memory", "entry-1", 2),
        target_namespace="user:local",
        target_path="/preferences/editor.md",
        diff={"op": "add", "description": "Synthetic preference"},
        reason="Explicit user request",
        status=PromotionStatus.PENDING,
        revision=1,
        source_refs=(source_ref,),
    )
    page = MemoryEntryPage(entries=(entry,), next_cursor="entry-1", has_more=True)

    for record in (space, entry, revision, link, candidate, job, proposal, page):
        assert type(record).from_dict(record.to_dict()) == record


def test_workspace_link_entry_carries_a_valid_url_without_host_paths() -> None:
    entry = MemoryEntry(
        entry_id="entry-link",
        space_id="space-1",
        path="/links/unchain.md",
        name="Unchain",
        description="Project reference",
        kind=MemoryEntryKind.LINK,
        revision=1,
        link_url="https://example.test/unchain?q=memory",
    )

    assert MemoryEntry.from_dict(entry.to_dict()) == entry
    assert entry.content_ref is None
    assert entry.media_type == ""


@pytest.mark.parametrize(
    "link_url",
    [
        "https://user:password@example.test/project",
        "https://user@example.test/project",
        "https://example.test/project?password=not-safe",
        "https://example.test/project?api%255Fkey=not-safe",
        "https://example.test/project?api%2Fkey=not-safe",
        "https://example.test/project?ｃｌｉｅｎｔ－ｓｅｃｒｅｔ=not-safe",
        "https://example.test/project#refresh%5Ftoken=not-safe",
        "https://storage.example.test/blob?sv=2024&sp=r&sig=abcDEF0123456789%2Fqwerty",
        "https://example.test/project?signature=abcDEF0123456789",
        "https://example.test/project?auth=abcDEF0123456789",
        "https://example.test/project?jwt=abc.DEF.0123456789",
        "https://hooks.slack.com/services/T000/B000/abcDEF0123456789",
        "https://discord.com/api/webhooks/123456789/abcDEF0123456789",
        "https://maker.ifttt.com/trigger/build/with/key/abcDEF0123456789",
        "https://api.telegram.org/bot123456:abcDEF0123456789/getUpdates",
    ],
)
def test_workspace_link_entry_rejects_credential_bearing_urls(link_url: str) -> None:
    with pytest.raises(ValueError, match="credential"):
        MemoryEntry(
            entry_id="entry-sensitive-link",
            space_id="space-1",
            path="/links/sensitive",
            name="Sensitive link",
            description="Must never be stored",
            kind=MemoryEntryKind.LINK,
            revision=1,
            link_url=link_url,
        )


@pytest.mark.parametrize(
    "kind,media_type,link_url",
    [
        (MemoryEntryKind.FOLDER, "text/plain", ""),
        (MemoryEntryKind.FOLDER, "", "https://example.test"),
        (MemoryEntryKind.MARKDOWN, "text/markdown", "https://example.test"),
        (MemoryEntryKind.IMAGE, "text/plain", ""),
        (MemoryEntryKind.LINK, "", "file:///etc/passwd"),
        (MemoryEntryKind.LINK, "", ""),
    ],
)
def test_workspace_entry_rejects_incompatible_content_metadata(
    kind: MemoryEntryKind,
    media_type: str,
    link_url: str,
) -> None:
    with pytest.raises(ValueError):
        MemoryEntry(
            entry_id="entry-invalid",
            space_id="space-1",
            path="/invalid",
            name="Invalid",
            description="Synthetic",
            kind=kind,
            revision=1,
            media_type=media_type,
            link_url=link_url,
        )


@pytest.mark.parametrize("revision", [None, 0, -1, True, 1.2, "1"])
def test_resource_ref_rejects_ambiguous_revisions(revision: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ResourceRef.from_dict(
            {
                "schema": "unchain.resource_ref.v1",
                "kind": "artifact",
                "id": "artifact-1",
                "revision": revision,
                "fragment": "",
            }
        )


@pytest.mark.parametrize(
    "path",
    [
        "../notes/a.md",
        "/notes/../secrets.md",
        r"C:\\Users\\person\\secret.txt",
        r"\\server\\share\\secret.txt",
        "file:///tmp/secret.txt",
        "/Users/person/secret.txt",
        "/home/person/secret.txt",
        "/tmp/secret.txt",
        "/Volumes/private/data",
        "/System/Library/keychains",
        "/Library/Keychains/login.keychain-db",
        "/dev/null",
        "/proc/self/environ",
        "/C:/Users/person/secret.txt",
        "/windows/System32/config",
        "/ProgramData/vendor/credential",
        "/file:/tmp/secret.txt",
        "/notes/%2e%2e/secret",
        "/notes/%2Fetc/passwd",
        "/notes/line\nbreak.md",
        "/notes/\u202esecret.md",
    ],
)
def test_workspace_rejects_host_and_traversal_paths(path: str) -> None:
    with pytest.raises(ValueError):
        MemoryEntry(
            entry_id="entry-1",
            space_id="space-1",
            path=path,
            name="Bad path",
            description="Synthetic",
            kind=MemoryEntryKind.MARKDOWN,
            revision=1,
        )


@pytest.mark.parametrize(
    "field",
    [
        "password",
        "api_key",
        "access_token",
        "secret_value",
        "client_secret",
        "refresh_token",
        "auth_token",
        "bearer_token",
        "secret_key",
        "authorization",
        "oauth_token",
        "personal_access_token",
        "github_token",
        "x_api_key",
        "client_password",
        "database_password",
        "encryption_key",
        "password_url",
        "raw_password_hash",
        "client_secret_value_ref",
        "password_ref",
        "pass-word",
        "ｐａｓｓｗｏｒｄ",
        "passwords",
        "client_secrets",
        "access_tokens",
        "api_keys",
        "github_tokens",
        "access_keys",
        "session_cookies",
        "authorizations",
        "password1",
        "api_key1",
    ],
)
def test_nested_plaintext_secret_fields_are_rejected(field: str) -> None:
    with pytest.raises(ValueError, match="plaintext secret|opaque credential"):
        JournalEvent(
            event_id="event-1",
            event_type="tool.started",
            attempt=AttemptRef(GenerationRef("execution-1", "generation-1"), "attempt-1"),
            operation=OperationRef("operation-1", SHA_A),
            store_seq=1,
            payload={"nested": {field: "do-not-store"}},
        )

    safe = JournalEvent(
        event_id="event-2",
        event_type="context.build",
        attempt=AttemptRef(GenerationRef("execution-1", "generation-1"), "attempt-1"),
        operation=OperationRef("operation-2", SHA_B),
        store_seq=2,
        payload={
            "token_budget": 12_000,
            "input_tokens": 100,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "cache_write_5m_tokens": None,
            "cache_write_1h_tokens": None,
            "uncached_tokens": None,
            "visible_tokens": None,
            "reasoning_tokens": None,
            "secret_handle": {
                "schema": "unchain.opaque_secret_handle.v1",
                "handle_id": "secret-handle-1",
                "label": "Synthetic login",
                "scope": "session",
            },
            "oauth_token_ref": _resource("opaque-ref-1").to_dict(),
            "api_key_id": "opaque-id-1",
            "raw_password_hash": SHA_A,
            "token_usage": {"input_tokens": 40, "output_tokens": 5},
        },
    )
    assert safe.payload["token_budget"] == 12_000
    assert safe.payload["cache_read_tokens"] is None

    with pytest.raises(ValueError, match="opaque credential"):
        JournalEvent(
            event_id="event-3",
            event_type="context.build",
            attempt=AttemptRef(GenerationRef("execution-1", "generation-1"), "attempt-1"),
            operation=OperationRef("operation-3", SHA_A),
            store_seq=3,
            payload={"token_usage": {"input_tokens": -1}},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cache_read_tokens", -1),
        ("cache_read_tokens", True),
        ("cache_read_tokens", "unknown"),
        ("request_tokens", None),
        ("access_token", None),
    ],
)
def test_nullable_token_metrics_do_not_relax_credential_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match="plaintext secret|opaque credential"):
        JournalEvent(
            event_id="event-token-metric-negative",
            event_type="context.build",
            attempt=AttemptRef(
                GenerationRef("execution-1", "generation-1"),
                "attempt-1",
            ),
            operation=OperationRef("operation-token-metric-negative", SHA_A),
            store_seq=4,
            payload={field: value},
        )


@pytest.mark.parametrize(
    "field",
    ["passwords", "client_secrets", "access_tokens", "api_keys", "github_tokens"],
)
def test_plural_plaintext_secret_arrays_are_rejected(field: str) -> None:
    with pytest.raises(ValueError, match="plaintext secret"):
        JournalEvent(
            event_id="event-plural",
            event_type="tool.started",
            attempt=AttemptRef(GenerationRef("execution-1", "generation-1"), "attempt-1"),
            operation=OperationRef("operation-plural", SHA_A),
            store_seq=1,
            payload={field: ["do-not-store"]},
        )


@pytest.mark.parametrize(
    "fragment",
    [r"notes\..\secret", r"notes\section", "../secret", "notes/../secret"],
)
def test_resource_fragments_reject_path_syntax_and_traversal(fragment: str) -> None:
    with pytest.raises(ValueError):
        ResourceRef("artifact", "artifact-1", 1, fragment)


def test_invalid_hash_schema_and_non_finite_json_are_rejected() -> None:
    with pytest.raises(ValueError):
        ArtifactRef(_resource(), "text/plain", 1, "short")
    with pytest.raises(ValueError):
        ResourceRef.from_dict(
            {
                "schema": "unchain.resource_ref.v2",
                "kind": "artifact",
                "id": "artifact-1",
                "revision": 1,
                "fragment": "",
            }
        )
    with pytest.raises(ValueError, match="invalid object key"):
        JournalEvent(
            event_id="event-2",
            event_type="test",
            attempt=AttemptRef(GenerationRef("execution-1", "generation-1"), "attempt-1"),
            operation=OperationRef("operation-2", SHA_B),
            store_seq=2,
            payload={"valid": 1, 2: "invalid"},
        )
    with pytest.raises(ValueError):
        JournalEvent(
            event_id="event-1",
            event_type="test",
            attempt=AttemptRef(GenerationRef("execution-1", "generation-1"), "attempt-1"),
            operation=OperationRef("operation-1", SHA_A),
            store_seq=1,
            payload={"score": float("nan")},
        )


def test_journal_page_enforces_event_order_and_cursor_identity() -> None:
    attempt = AttemptRef(GenerationRef("execution-1", "generation-1"), "attempt-1")
    first = JournalEvent("event-1", "message.user", attempt, OperationRef("op-1", SHA_A), 1)
    second = JournalEvent("event-2", "message.assistant", attempt, OperationRef("op-2", SHA_B), 2)

    valid = JournalPage(
        events=(first, second),
        next_cursor=EventCursor(2, "event-2"),
        has_more=False,
    )
    assert valid.next_cursor == EventCursor(2, "event-2")

    with pytest.raises(ValueError, match="increasing"):
        JournalPage(
            events=(second, first),
            next_cursor=EventCursor(1, "event-1"),
            has_more=False,
        )
    with pytest.raises(ValueError, match="final event"):
        JournalPage(
            events=(first, second),
            next_cursor=EventCursor(99, "wrong"),
            has_more=False,
        )
    with pytest.raises(ValueError, match="empty"):
        JournalPage(events=(), next_cursor=None, has_more=True)
    duplicate_id = JournalEvent(
        "event-1",
        "message.assistant",
        attempt,
        OperationRef("op-3", SHA_A),
        3,
    )
    with pytest.raises(ValueError, match="unique"):
        JournalPage(
            events=(first, duplicate_id),
            next_cursor=EventCursor(3, "event-1"),
        )
    foreign = JournalEvent(
        "event-foreign",
        "message.assistant",
        AttemptRef(GenerationRef("execution-2", "generation-1"), "attempt-1"),
        OperationRef("op-4", SHA_B),
        4,
    )
    with pytest.raises(ValueError, match="one execution"):
        JournalPage(
            events=(first, foreign),
            next_cursor=EventCursor(4, "event-foreign"),
        )


def test_new_model_packages_contain_no_pupu_ownership_strings() -> None:
    source_root = Path(__file__).parents[2] / "src" / "unchain"
    forbidden = ("owner_chat_id", "pupu://", "UNCHAIN_DATA_DIR", "flask", "sqlite")
    for package in (
        source_root / "journal",
        source_root / "context",
        source_root / "memory" / "workspace",
    ):
        for source_path in package.glob("*.py"):
            source = source_path.read_text(encoding="utf-8").casefold()
            assert not any(marker.casefold() in source for marker in forbidden), source_path
