from __future__ import annotations

from pathlib import Path

import pytest

from unchain.context.harness import ContextExecutionBindingHarness
from unchain.context.task_state_bootstrap import (
    PinnedTaskStateBootstrapBinding,
    PinnedTaskStateBootstrapHarness,
)
from unchain.journal import OperationRef, ResourceRef
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.memory.workspace import TaskStateService
from unchain.memory.workspace.ports import (
    BoundPinnedTaskStateRepository,
    BoundWorkspaceReferenceAuthorizer,
    RepositoryConflictError,
    RepositoryScopeError,
    WorkspaceRepositoryError,
)
from unchain.persistence.sqlite_memory_v2 import SQLiteMemoryV2Store


class _References(BoundWorkspaceReferenceAuthorizer):
    def __init__(
        self,
        binding_id: str,
        allowed: set[ResourceRef],
        *,
        failure: Exception | None = None,
    ) -> None:
        super().__init__(binding_id)
        self.allowed = allowed
        self.failure = failure
        self.calls: list[ResourceRef] = []

    def authorize(self, *, ref: ResourceRef) -> ResourceRef:
        self.calls.append(ref)
        if self.failure is not None:
            raise self.failure
        if ref not in self.allowed:
            raise RepositoryScopeError("unknown current-input event")
        return ref


class _TaskStateRepository(BoundPinnedTaskStateRepository):
    def __init__(
        self,
        binding_id: str,
        *,
        read_failure: Exception | None = None,
        cas_failure: Exception | None = None,
        hide_current: bool = False,
    ) -> None:
        super().__init__(binding_id)
        self.state = None
        self.operations: dict[str, tuple[str, object]] = {}
        self.read_failure = read_failure
        self.cas_failure = cas_failure
        self.hide_current = hide_current
        self.read_count = 0

    def current(self):
        self.read_count += 1
        if self.read_failure is not None:
            raise self.read_failure
        if self.hide_current:
            return None
        return self.state

    def replay(self, *, operation: OperationRef):
        previous = self.operations.get(operation.operation_id)
        if previous is None:
            return None
        payload_sha256, state = previous
        if payload_sha256 != operation.payload_sha256:
            raise RepositoryConflictError("operation payload changed")
        return state

    def compare_and_swap(self, *, state, expected_revision, operation):
        if self.cas_failure is not None:
            raise self.cas_failure
        previous = self.operations.get(operation.operation_id)
        if previous is not None:
            payload_sha256, persisted = previous
            if payload_sha256 != operation.payload_sha256:
                raise RepositoryConflictError("operation payload changed")
            return persisted
        actual_revision = self.state.revision if self.state is not None else None
        if actual_revision != expected_revision:
            raise RepositoryConflictError("task state revision changed")
        self.state = state
        self.operations[operation.operation_id] = (
            operation.payload_sha256,
            state,
        )
        return state


def _context() -> HarnessContext:
    state = RunState()
    state.session_state.session_id = "chat-bootstrap"
    return HarnessContext(
        state=state,
        phase="bootstrap",
        event={"run_id": "attempt-bootstrap"},
    )


def _service(
    *,
    repository: BoundPinnedTaskStateRepository,
    event_ref: ResourceRef,
    references: BoundWorkspaceReferenceAuthorizer | None = None,
) -> TaskStateService:
    return TaskStateService(
        repository=repository,
        references=references
        or _References(repository.binding_id, {event_ref}),
    )


def _harness(
    *,
    service: TaskStateService,
    event_ref_resolver,
    objective: str = "Keep the complete task picture pinned",
    input_kind: str = "user_message",
) -> PinnedTaskStateBootstrapHarness:
    binding = PinnedTaskStateBootstrapBinding(
        task_state=service,
        objective=objective,
        current_input_event_ref_resolver=event_ref_resolver,
        input_kind=input_kind,
    )
    return PinnedTaskStateBootstrapHarness(
        binding_resolver=lambda context: binding,
    )


def test_contract_runs_only_during_bootstrap_after_context_execution_binding() -> None:
    event_ref = ResourceRef("context_event", "event-contract", 1)
    repository = _TaskStateRepository("chat-contract")
    harness = _harness(
        service=_service(repository=repository, event_ref=event_ref),
        event_ref_resolver=lambda: event_ref,
    )

    assert harness.phases == ("bootstrap",)
    assert harness.order > ContextExecutionBindingHarness.order
    assert harness.order < 0


def test_first_user_message_creates_objective_with_exact_event_provenance() -> None:
    event_ref = ResourceRef("context_event", "event-first-user-message", 1)
    repository = _TaskStateRepository("chat-first-user-message")
    references = _References(repository.binding_id, {event_ref})
    service = _service(
        repository=repository,
        event_ref=event_ref,
        references=references,
    )
    harness = _harness(
        service=service,
        event_ref_resolver=lambda: event_ref,
    )

    assert harness.apply(_context()) is None

    state = service.get()
    assert state is not None
    assert state.revision == 1
    assert state.objective == "Keep the complete task picture pinned"
    assert state.source_event_refs == (event_ref,)
    assert references.calls == [event_ref]
    [operation_id] = repository.operations
    assert operation_id.startswith("pinned-task-state-bootstrap-v1-")


def test_stable_operation_id_replays_when_current_reader_is_stale() -> None:
    event_ref = ResourceRef("context_event", "event-idempotent-replay", 1)
    repository = _TaskStateRepository(
        "chat-idempotent-replay",
        hide_current=True,
    )
    service = _service(repository=repository, event_ref=event_ref)
    harness = _harness(
        service=service,
        event_ref_resolver=lambda: event_ref,
    )

    harness.apply(_context())
    [first_operation_id] = repository.operations
    harness.apply(_context())

    assert tuple(repository.operations) == (first_operation_id,)
    assert repository.state is not None
    assert repository.state.revision == 1


def test_restart_observes_existing_state_and_does_not_resolve_input_again(
    tmp_path: Path,
) -> None:
    event_ref = ResourceRef("context_event", "event-restart", 1)
    database_path = tmp_path / "context_v2.sqlite3"
    object_directory = tmp_path / "objects"
    first_store = SQLiteMemoryV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    first_service = _service(
        repository=first_store.bind_task_state(binding_id="chat-restart"),
        event_ref=event_ref,
    )
    _harness(
        service=first_service,
        event_ref_resolver=lambda: event_ref,
        objective="Original durable objective",
    ).apply(_context())

    cold_store = SQLiteMemoryV2Store(
        database_path=database_path,
        object_directory=object_directory,
    )
    cold_service = _service(
        repository=cold_store.bind_task_state(binding_id="chat-restart"),
        event_ref=event_ref,
    )

    def unexpected_ref_read():
        raise AssertionError("existing state must not re-read current input")

    _harness(
        service=cold_service,
        event_ref_resolver=unexpected_ref_read,
        objective="A later turn must not replace the bootstrap objective",
    ).apply(_context())

    recovered = cold_service.get()
    assert recovered is not None
    assert recovered.revision == 1
    assert recovered.objective == "Original durable objective"
    assert recovered.source_event_refs == (event_ref,)


def test_interaction_resume_never_reads_or_creates_task_state() -> None:
    event_ref = ResourceRef("context_event", "event-interaction-resume", 1)
    repository = _TaskStateRepository(
        "chat-interaction-resume",
        read_failure=AssertionError("interaction resume must not read task state"),
    )
    harness = _harness(
        service=_service(repository=repository, event_ref=event_ref),
        event_ref_resolver=lambda: (_ for _ in ()).throw(
            AssertionError("interaction resume must not resolve current input")
        ),
        input_kind="interaction_resume",
    )

    assert harness.apply(_context()) is None
    assert repository.read_count == 0
    assert repository.operations == {}


@pytest.mark.parametrize(
    ("failure_boundary", "expected_message"),
    [
        ("read", "reader unavailable"),
        ("ref", "event ref unavailable"),
        ("authorize", "event authorization unavailable"),
        ("cas", "task state persistence unavailable"),
    ],
)
def test_bootstrap_failures_propagate_and_leave_state_uncreated(
    failure_boundary: str,
    expected_message: str,
) -> None:
    event_ref = ResourceRef("context_event", f"event-failure-{failure_boundary}", 1)
    read_failure = (
        WorkspaceRepositoryError(expected_message)
        if failure_boundary == "read"
        else None
    )
    cas_failure = (
        WorkspaceRepositoryError(expected_message)
        if failure_boundary == "cas"
        else None
    )
    repository = _TaskStateRepository(
        f"chat-failure-{failure_boundary}",
        read_failure=read_failure,
        cas_failure=cas_failure,
    )
    reference_failure = (
        WorkspaceRepositoryError(expected_message)
        if failure_boundary == "authorize"
        else None
    )
    references = _References(
        repository.binding_id,
        {event_ref},
        failure=reference_failure,
    )

    def resolve_ref():
        if failure_boundary == "ref":
            raise WorkspaceRepositoryError(expected_message)
        return event_ref

    harness = _harness(
        service=_service(
            repository=repository,
            event_ref=event_ref,
            references=references,
        ),
        event_ref_resolver=resolve_ref,
    )

    with pytest.raises(WorkspaceRepositoryError, match=expected_message):
        harness.apply(_context())
    assert repository.state is None
    assert repository.operations == {}


@pytest.mark.parametrize(
    "event_ref",
    [
        ResourceRef("artifact", "artifact-current-input", 1),
        ResourceRef("context_event", "event-fragmented", 1, "payload"),
    ],
)
def test_current_input_provenance_must_be_a_bare_context_event(
    event_ref: ResourceRef,
) -> None:
    repository = _TaskStateRepository("chat-invalid-ref")
    harness = _harness(
        service=_service(repository=repository, event_ref=event_ref),
        event_ref_resolver=lambda: event_ref,
    )

    with pytest.raises(ValueError, match="bare context_event"):
        harness.apply(_context())
    assert repository.state is None
