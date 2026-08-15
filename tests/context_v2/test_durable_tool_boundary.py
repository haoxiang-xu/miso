from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields

import pytest

from unchain.context import (
    ArtifactService,
    CanonicalSemanticEventProjector,
    DurableToolBoundary,
    DurableToolBoundaryCorruptError,
    DurableToolApprovalState,
    DurableToolAuthorization,
    DurableToolExecutionDisposition,
    DurableToolExecutionSubject,
    DurableToolExecutionUncertainError,
    DurableToolRouteKind,
    MAX_PREVIEW_BYTES,
)
from unchain.execution import ExecutionFence
from unchain.context.ports import BoundArtifactRepository
from unchain.journal import (
    ArtifactRef,
    AttemptRef,
    BoundToolReceiptIndex,
    DurableEventSink,
    EventCursor,
    GenerationRef,
    JournalAppendRequest,
    JournalAppendResult,
    JournalConflictError,
    JournalEvent,
    JournalPage,
    OperationRef,
    ResourceRef,
    ToolExecutionReceiptLookup,
)
from unchain.journal.snapshot import capture_journal_snapshot


ATTEMPT = AttemptRef(
    GenerationRef("execution-1", "generation-1"),
    "attempt-1",
)


class _Journal(BoundToolReceiptIndex):
    def __init__(
        self,
        order: list[str],
        attempt: AttemptRef = ATTEMPT,
    ) -> None:
        super().__init__(attempt.generation.execution_id)
        self.order = order
        self.events: list[JournalEvent] = []
        self.operations: dict[str, JournalEvent] = {}
        self.fail_next = False
        self.lock = threading.Lock()

    def append(self, *, request: JournalAppendRequest) -> JournalAppendResult:
        with self.lock:
            if self.fail_next:
                self.fail_next = False
                raise OSError("journal unavailable")
            previous = self.operations.get(request.operation.operation_id)
            if previous is not None:
                if previous.operation != request.operation:
                    raise JournalConflictError("operation payload conflict")
                return JournalAppendResult(
                    previous,
                    EventCursor(previous.store_seq, previous.event_id),
                    duplicate=True,
                )
            self.order.append(f"journal:{request.event_type}")
            event = JournalEvent(
                event_id=request.event_id,
                event_type=request.event_type,
                attempt=request.attempt,
                operation=request.operation,
                store_seq=len(self.events) + 1,
                payload=request.payload,
                resource_refs=request.resource_refs,
            )
            self.events.append(event)
            self.operations[request.operation.operation_id] = event
            return JournalAppendResult(
                event,
                EventCursor(event.store_seq, event.event_id),
                duplicate=False,
            )

    def read(
        self,
        *,
        after: EventCursor | None = None,
        limit: int = 100,
    ) -> JournalPage:
        start = after.store_seq if after is not None else 0
        events = tuple(self.events[start : start + limit])
        cursor = (
            EventCursor(events[-1].store_seq, events[-1].event_id)
            if events
            else after
        )
        return JournalPage(
            events=events,
            next_cursor=cursor,
            has_more=start + len(events) < len(self.events),
        )

    def capture_snapshot(self, *, max_events=10_000, max_bytes=32 * 1024 * 1024):
        del max_events, max_bytes
        return capture_journal_snapshot(
            execution_id=self.execution_id,
            events=tuple(self.events),
        )

    def lookup_tool_execution_receipts(self, *, attempt, call_id):
        candidates = tuple(
            event
            for event in self.events
            if event.attempt == attempt
            and event.payload.get("call_id") == call_id
            and event.event_type
            in {
                "tool_call",
                "tool.started",
                "tool.subagent_completion.sealed",
                "tool_result",
                "tool.result",
            }
        )
        return ToolExecutionReceiptLookup(
            attempt=attempt,
            call_id=call_id,
            events=candidates[:4],
            overflow=len(candidates) > 4,
        )


class _Artifacts(BoundArtifactRepository):
    def __init__(
        self,
        order: list[str],
        execution_id: str = ATTEMPT.generation.execution_id,
    ) -> None:
        super().__init__(execution_id)
        self.order = order
        self.by_operation: dict[str, tuple[OperationRef, ArtifactRef, bytes]] = {}
        self.by_id: dict[str, tuple[ArtifactRef, bytes]] = {}

    def put(self, *, content, media_type, operation, preview=""):
        previous = self.by_operation.get(operation.operation_id)
        if previous is not None:
            if previous[0] != operation:
                raise AssertionError("artifact operation payload conflict")
            return previous[1]
        self.order.append("artifact:put")
        digest = hashlib.sha256(content).hexdigest()
        ref = ResourceRef("artifact", digest, 1)
        artifact = ArtifactRef(ref, media_type, len(content), digest, preview)
        record = (operation, artifact, content)
        self.by_operation[operation.operation_id] = record
        self.by_id[ref.resource_id] = (artifact, content)
        return artifact

    def read_verified(self, *, artifact, offset=0, limit=65_536):
        stored, content = self.by_id[artifact.ref.resource_id]
        assert stored == artifact
        return content[offset : offset + limit]

    def read_full_verified(self, *, artifact):
        stored, content = self.by_id[artifact.ref.resource_id]
        assert stored.ref == artifact.ref
        assert stored.byte_length == artifact.byte_length
        assert stored.sha256 == artifact.sha256
        return content


def _boundary(*, redact: bool = False, attempt: AttemptRef = ATTEMPT):
    order: list[str] = []
    journal = _Journal(order, attempt)

    def sanitize(content: bytes, media_type: str) -> bytes:
        assert media_type == "application/json"
        if redact:
            return content.replace(b"secret-value", b"[REDACTED]")
        return content

    artifacts = ArtifactService(
        _Artifacts(order, attempt.generation.execution_id),
        sanitizer=sanitize,
    )
    projector = CanonicalSemanticEventProjector(
        attempt=attempt,
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )
    sink = DurableEventSink(journal, attempt, projector)
    return DurableToolBoundary(
        attempt=attempt,
        projector=projector,
        sink=sink,
    ), journal, order


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _persist_intent(
    boundary: DurableToolBoundary,
    *,
    tool_name: str = "lookup",
    call_id: str = "call-1",
    iteration: int = 0,
    arguments=None,
) -> tuple[JournalAppendResult, dict]:
    durable_arguments = {"query": "safe"} if arguments is None else arguments
    appended = boundary.sink(
        {
            "type": "tool_call",
            "run_id": boundary.attempt.attempt_id,
            "iteration": iteration,
            "tool_name": tool_name,
            "call_id": call_id,
            "arguments": durable_arguments,
        }
    )
    assert isinstance(appended, JournalAppendResult)
    return appended, durable_arguments


def _subject(
    *,
    intent_cursor: EventCursor = EventCursor(1, "event-intent"),
    original_arguments_sha256: str = "1" * 64,
    effective_arguments_sha256: str = "5" * 64,
    approval_state: DurableToolApprovalState = DurableToolApprovalState.APPROVED,
    approval_request_sha256: str = "2" * 64,
    approval_receipt_sha256: str = "3" * 64,
    route_kind: DurableToolRouteKind = DurableToolRouteKind.NORMAL,
    route_manifest_sha256: str = "4" * 64,
    terminal_handler_manifest_sha256: str = "6" * 64,
    execution_fence: ExecutionFence = ExecutionFence(
        "execution-1",
        "owner-1",
        1,
    ),
) -> DurableToolExecutionSubject:
    return DurableToolExecutionSubject(
        intent_cursor=intent_cursor,
        original_arguments_sha256=original_arguments_sha256,
        effective_arguments_sha256=effective_arguments_sha256,
        approval_state=approval_state,
        approval_request_sha256=approval_request_sha256,
        approval_receipt_sha256=approval_receipt_sha256,
        route_kind=route_kind,
        route_manifest_sha256=route_manifest_sha256,
        terminal_handler_manifest_sha256=terminal_handler_manifest_sha256,
        execution_fence=execution_fence,
    )


def _subject_for_intent(
    appended: JournalAppendResult,
    arguments: dict,
    **changes,
) -> DurableToolExecutionSubject:
    values = {
        "intent_cursor": appended.cursor,
        "original_arguments_sha256": _digest(arguments),
        "effective_arguments_sha256": _digest(arguments),
        **changes,
    }
    return _subject(**values)


def test_fresh_authorization_persists_started_before_execution_permission() -> None:
    boundary, journal, order = _boundary()
    intent, arguments = _persist_intent(
        boundary,
        tool_name="shell",
        iteration=2,
    )
    subject = _subject_for_intent(intent, arguments)

    authorization = boundary.authorize_execution(
        tool_name="shell",
        call_id="call-1",
        iteration=2,
        subject=subject,
    )

    assert authorization.disposition is DurableToolExecutionDisposition.EXECUTE
    assert authorization.should_execute is True
    assert authorization.started_cursor.store_seq == 2
    assert order == ["journal:tool_call", "journal:tool.started"]
    assert journal.events[1].payload == {
        "run_id": "attempt-1",
        "iteration": 2,
        "tool_name": "shell",
        "call_id": "call-1",
        "execution_subject": subject.to_dict(),
        "execution_subject_sha256": subject.sha256,
    }


def test_uncertain_started_receipt_never_authorizes_reexecution() -> None:
    boundary, _journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary, tool_name="shell")
    subject = _subject_for_intent(intent, arguments)
    boundary.authorize_execution(
        tool_name="shell",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )

    with pytest.raises(DurableToolExecutionUncertainError):
        boundary.authorize_execution(
            tool_name="shell",
            call_id="call-1",
            iteration=0,
            subject=subject,
        )


def test_result_persistence_rejects_authorization_subclasses() -> None:
    boundary, journal, order = _boundary()
    intent, arguments = _persist_intent(boundary)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=_subject_for_intent(intent, arguments),
    )

    class ForgedAuthorization(DurableToolAuthorization):
        pass

    forged = ForgedAuthorization(
        **{
            item.name: getattr(authorization, item.name)
            for item in fields(DurableToolAuthorization)
        }
    )
    with pytest.raises(
        DurableToolBoundaryCorruptError,
        match="execute authorization",
    ):
        boundary.persist_result(forged, {"bad": True})

    assert [event.event_type for event in journal.events] == [
        "tool_call",
        "tool.started",
    ]
    assert order == ["journal:tool_call", "journal:tool.started"]


def test_result_is_sanitized_artifactized_and_journaled_before_becoming_visible() -> None:
    boundary, journal, order = _boundary(redact=True)
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )

    receipt = boundary.persist_result(
        authorization,
        {"value": "secret-value"},
    )

    assert order == [
        "journal:tool_call",
        "journal:tool.started",
        "artifact:put",
        "journal:tool_result",
    ]
    assert receipt.visible_result == {"value": "[REDACTED]"}
    assert receipt.artifact.ref == journal.events[-1].resource_refs[0]
    assert receipt.cursor.store_seq == 3
    assert receipt.attempt == ATTEMPT
    assert receipt.tool_name == "lookup"
    assert receipt.call_id == "call-1"
    assert receipt.iteration == 0
    assert receipt.execution_subject == subject
    assert receipt.execution_subject_sha256 == subject.sha256
    assert "secret-value" not in str(journal.events[-1].to_dict())


def test_prepared_completion_artifact_must_bind_exact_tool_identity() -> None:
    boundary, journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    result = boundary.projector.artifacts.artifactize_tool_result(
        {"ok": True},
        operation_id="artifact.test.prepared-result",
    )
    completion = boundary.projector.artifacts.artifactize_tool_completion(
        {
            "schema": "unchain.durable_tool_completion.v1",
            "attempt": ATTEMPT.to_dict(),
            "tool_name": "lookup",
            "call_id": "forged-call",
            "iteration": 0,
            "execution_subject": subject.to_dict(),
            "execution_subject_sha256": subject.sha256,
            "result_artifact": result.artifact.to_dict(),
            "visible_result": result.event_fields()["result"],
            "should_observe": True,
        },
        operation_id="artifact.test.prepared-completion",
    )

    with pytest.raises(
        DurableToolBoundaryCorruptError,
        match="completion.*identity",
    ):
        boundary.persist_prepared_result(
            authorization,
            artifactization=result,
            completion_artifactization=completion,
        )

    assert [event.event_type for event in journal.events] == [
        "tool_call",
        "tool.started",
    ]


def test_persisted_terminal_result_is_reused_without_new_execution_or_artifact() -> None:
    boundary, _journal, order = _boundary()
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    written = boundary.persist_result(authorization, {"ok": True})

    recovered = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )

    assert recovered.disposition is DurableToolExecutionDisposition.REUSE
    assert recovered.should_execute is False
    assert recovered.visible_result == written.visible_result
    assert recovered.result_artifact == written.artifact
    assert order == [
        "journal:tool_call",
        "journal:tool.started",
        "artifact:put",
        "journal:tool_result",
    ]


def test_terminal_result_reuses_canonical_whitespace_bounded_preview() -> None:
    boundary, _journal, order = _boundary()
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    json_prefix_bytes = len(b'{"value":"')
    value = (
        "a" * (MAX_PREVIEW_BYTES - json_prefix_bytes - 1)
        + " "
        + "tail"
    )

    written = boundary.persist_result(authorization, {"value": value})
    repository = boundary.projector.artifacts._repository
    _stored_artifact, content = repository.by_id[
        written.artifact.ref.resource_id
    ]
    raw_preview = content[:MAX_PREVIEW_BYTES].decode(
        "utf-8",
        errors="ignore",
    )

    assert raw_preview.endswith(" ")
    assert written.artifact.preview == raw_preview.strip()
    recovered = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    assert recovered.disposition is DurableToolExecutionDisposition.REUSE
    assert recovered.visible_result == written.visible_result
    assert order == [
        "journal:tool_call",
        "journal:tool.started",
        "artifact:put",
        "journal:tool_result",
    ]


def test_terminal_result_reuses_nfc_canonical_artifact_preview() -> None:
    boundary, _journal, order = _boundary()
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )

    written = boundary.persist_result(
        authorization,
        {"value": "e\u0301"},
    )

    assert written.artifact.preview == '{"value":"é"}'
    recovered = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    assert recovered.disposition is DurableToolExecutionDisposition.REUSE
    assert recovered.visible_result == {"value": "e\u0301"}
    assert recovered.result_artifact == written.artifact
    assert order == [
        "journal:tool_call",
        "journal:tool.started",
        "artifact:put",
        "journal:tool_result",
    ]


def test_reuse_verifies_the_full_artifact_before_returning_visible_result() -> None:
    boundary, _journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    written = boundary.persist_result(authorization, {"ok": True})
    repository = boundary.projector.artifacts._repository
    stored_artifact, _content = repository.by_id[
        written.artifact.ref.resource_id
    ]
    repository.by_id[written.artifact.ref.resource_id] = (
        stored_artifact,
        b"tampered",
    )

    with pytest.raises(DurableToolBoundaryCorruptError, match="artifact"):
        boundary.authorize_execution(
            tool_name="lookup",
            call_id="call-1",
            iteration=0,
            subject=subject,
        )


def test_artifact_success_followed_by_journal_failure_remains_uncertain_and_retryable() -> None:
    boundary, journal, order = _boundary()
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    journal.fail_next = True

    with pytest.raises(OSError, match="journal unavailable"):
        boundary.persist_result(authorization, {"ok": True})

    assert order == ["journal:tool_call", "journal:tool.started", "artifact:put"]
    with pytest.raises(DurableToolExecutionUncertainError):
        boundary.authorize_execution(
            tool_name="lookup",
            call_id="call-1",
            iteration=0,
            subject=subject,
        )
    recovered = boundary.persist_result(authorization, {"ok": True})
    assert recovered.visible_result == {"ok": True}
    assert order == [
        "journal:tool_call",
        "journal:tool.started",
        "artifact:put",
        "journal:tool_result",
    ]


def test_forged_or_mismatched_authorization_fails_closed() -> None:
    first, _journal, _order = _boundary()
    second, _other_journal, _other_order = _boundary()
    intent, arguments = _persist_intent(first, tool_name="shell")
    subject = _subject_for_intent(intent, arguments)
    authorization = first.authorize_execution(
        tool_name="shell",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )

    with pytest.raises(DurableToolBoundaryCorruptError):
        second.persist_result(authorization, {"ok": True})


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"original_arguments_sha256": "a" * 64}, "original arguments"),
        ({"effective_arguments_sha256": "b" * 64}, "effective arguments"),
        ({"approval_request_sha256": "c" * 64}, "approval request"),
        ({"approval_receipt_sha256": "d" * 64}, "approval receipt"),
        ({"route_kind": DurableToolRouteKind.PLUGIN}, "route kind"),
        ({"route_manifest_sha256": "e" * 64}, "route manifest"),
        ({"terminal_handler_manifest_sha256": "f" * 64}, "terminal handler"),
    ],
)
def test_recovery_rejects_any_execution_subject_change(
    changes: dict,
    field: str,
) -> None:
    boundary, _journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    boundary.persist_result(authorization, {"ok": True})

    with pytest.raises(DurableToolBoundaryCorruptError, match=field):
        changed_subject = _subject_for_intent(intent, arguments, **changes)
        boundary.authorize_execution(
            tool_name="lookup",
            call_id="call-1",
            iteration=0,
            subject=changed_subject,
        )


def test_started_and_result_receipts_bind_the_exact_execution_subject() -> None:
    boundary, journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    boundary.persist_result(authorization, {"ok": True})

    assert journal.events[1].payload["execution_subject"] == subject.to_dict()
    assert journal.events[2].payload["execution_subject"] == subject.to_dict()
    assert journal.events[1].payload["execution_subject_sha256"] == subject.sha256
    assert journal.events[2].payload["execution_subject_sha256"] == subject.sha256


def test_terminal_result_can_be_reused_under_a_newer_live_fence() -> None:
    boundary, _journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary)
    recorded_subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=recorded_subject,
    )
    boundary.persist_result(authorization, {"ok": True})
    current_fence = ExecutionFence("execution-1", "owner-2", 2)

    reused = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=_subject_for_intent(
            intent,
            arguments,
            execution_fence=current_fence,
        ),
    )

    assert reused.disposition is DurableToolExecutionDisposition.REUSE
    assert reused.execution_subject == recorded_subject
    assert reused.current_execution_fence == current_fence


def test_terminal_reuse_rejects_a_non_advancing_foreign_owner_fence() -> None:
    boundary, _journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary)
    recorded_subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=recorded_subject,
    )
    boundary.persist_result(authorization, {"ok": True})

    with pytest.raises(DurableToolBoundaryCorruptError, match="execution fence"):
        boundary.authorize_execution(
            tool_name="lookup",
            call_id="call-1",
            iteration=0,
            subject=_subject_for_intent(
                intent,
                arguments,
                execution_fence=ExecutionFence("execution-1", "owner-2", 1),
            ),
        )


def test_terminal_reuse_rejects_a_stale_fencing_token() -> None:
    boundary, _journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary)
    recorded_subject = _subject_for_intent(
        intent,
        arguments,
        execution_fence=ExecutionFence("execution-1", "owner-1", 2),
    )
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=recorded_subject,
    )
    boundary.persist_result(authorization, {"ok": True})

    with pytest.raises(DurableToolBoundaryCorruptError, match="execution fence"):
        boundary.authorize_execution(
            tool_name="lookup",
            call_id="call-1",
            iteration=0,
            subject=_subject_for_intent(
                intent,
                arguments,
                execution_fence=ExecutionFence("execution-1", "owner-1", 1),
            ),
        )


def test_terminal_reuse_rejects_inline_result_that_disagrees_with_artifact() -> None:
    boundary, journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary)
    subject = _subject_for_intent(intent, arguments)
    authorization = boundary.authorize_execution(
        tool_name="lookup",
        call_id="call-1",
        iteration=0,
        subject=subject,
    )
    boundary.persist_result(authorization, {"ok": True})
    persisted = journal.events[2]
    forged_payload = dict(persisted.payload)
    forged_payload["result"] = {"forged": True}
    journal.events[2] = JournalEvent(
        event_id=persisted.event_id,
        event_type=persisted.event_type,
        attempt=persisted.attempt,
        operation=persisted.operation,
        store_seq=persisted.store_seq,
        payload=forged_payload,
        resource_refs=persisted.resource_refs,
    )

    with pytest.raises(
        DurableToolBoundaryCorruptError,
        match="model-visible result",
    ):
        boundary.authorize_execution(
            tool_name="lookup",
            call_id="call-1",
            iteration=0,
            subject=subject,
        )


def test_execution_subject_rejects_incomplete_approval_binding() -> None:
    with pytest.raises(
        DurableToolBoundaryCorruptError,
        match="approval request and receipt",
    ):
        DurableToolExecutionSubject(
            intent_cursor=EventCursor(1, "event-intent"),
            original_arguments_sha256="1" * 64,
            effective_arguments_sha256="1" * 64,
            approval_state=DurableToolApprovalState.APPROVED,
            approval_request_sha256="",
            approval_receipt_sha256="3" * 64,
            route_kind=DurableToolRouteKind.NORMAL,
            route_manifest_sha256="4" * 64,
            terminal_handler_manifest_sha256="5" * 64,
            execution_fence=ExecutionFence("execution-1", "owner-1", 1),
        )


def test_not_required_approval_is_explicit_and_has_no_receipt_digests() -> None:
    subject = _subject(
        approval_state=DurableToolApprovalState.NOT_REQUIRED,
        approval_request_sha256="",
        approval_receipt_sha256="",
    )

    assert subject.approval_state is DurableToolApprovalState.NOT_REQUIRED


def test_authorization_requires_a_preceding_exact_tool_call_intent() -> None:
    boundary, _journal, order = _boundary()

    with pytest.raises(DurableToolBoundaryCorruptError, match="tool call intent"):
        boundary.authorize_execution(
            tool_name="lookup",
            call_id="call-1",
            iteration=0,
            subject=_subject(),
        )

    assert order == []


def test_blind_concurrent_subject_race_has_one_atomic_started_claim() -> None:
    boundary, journal, _order = _boundary()
    intent, arguments = _persist_intent(boundary)
    first_subject = _subject_for_intent(intent, arguments)
    second_subject = _subject_for_intent(
        intent,
        arguments,
        route_manifest_sha256="a" * 64,
    )
    original_recover = boundary.sink.recover_tool_side_effect
    blind_read = threading.Barrier(2)

    def recover_after_blind_read(call_id, *, page_size=100):
        recovery = original_recover(call_id, page_size=page_size)
        if recovery.state.value == "not-started":
            blind_read.wait(timeout=2)
        return recovery

    boundary.sink.recover_tool_side_effect = recover_after_blind_read

    def authorize(subject):
        return boundary.authorize_execution(
            tool_name="lookup",
            call_id="call-1",
            iteration=0,
            subject=subject,
        )

    outcomes = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(authorize, first_subject),
            executor.submit(authorize, second_subject),
        ]
        for future in futures:
            try:
                outcomes.append(future.result(timeout=3))
            except Exception as exc:
                outcomes.append(exc)

    authorizations = [
        value for value in outcomes if not isinstance(value, Exception)
    ]
    failures = [value for value in outcomes if isinstance(value, Exception)]
    assert len(authorizations) == 1
    assert authorizations[0].should_execute is True
    assert len(failures) == 1
    assert isinstance(failures[0], DurableToolBoundaryCorruptError)
    assert sum(event.event_type == "tool.started" for event in journal.events) == 1
