from __future__ import annotations

import hashlib

import pytest

from unchain.context import (
    ArtifactService,
    ContextCompileCoordinator,
    ContextExecutionBundle,
    ContextExecutionBundleError,
    ContextInputIngress,
    ContextRuntime,
    DurableHandoffRecorder,
    DurableContextRuntimeFactory,
    DurableToolBoundary,
    HandoffService,
    HostResolvedCurrentInput,
    HostResolvedInteractionInput,
    JournalContextRequestFactory,
)
from unchain.context.ports import BoundArtifactRepository
from unchain.context.projector import CanonicalSemanticEventProjector
from unchain.journal import (
    ArtifactRef,
    AttemptRef,
    BoundToolReceiptIndex,
    DurableEventSink,
    EventCursor,
    GenerationRef,
    JournalAppendResult,
    JournalEvent,
    JournalPage,
    ResourceRef,
    SemanticEventDraft,
    ToolExecutionReceiptLookup,
    capture_journal_snapshot,
)
from unchain.kernel.harness import HarnessContext
from unchain.kernel.state import RunState
from unchain.persistence import SQLiteContextV2Store
from unchain.providers.durable_turn_runtime import DurableProviderTurnMode
from unchain.providers.turn_ownership import ProviderTurnOwnership
from unchain.run_bundle import RunIdentity
from unchain.tools.output_management import (
    ToolOutputManager,
    ToolOutputPolicyVersionError,
)
from unchain.tools.tool import Tool
from unchain.tools.toolkit import Toolkit


class _Journal(BoundToolReceiptIndex):
    def __init__(self, execution_id):
        super().__init__(execution_id)
        self.events = []
        self.operations = {}

    def append(self, *, request):
        previous = self.operations.get(request.operation.operation_id)
        if previous is not None:
            prior_request, event = previous
            if prior_request != request:
                raise RuntimeError("journal operation conflict")
            return JournalAppendResult(
                cursor=EventCursor(event.store_seq, event.event_id),
                event=event,
                duplicate=True,
            )
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
        self.operations[request.operation.operation_id] = (request, event)
        return JournalAppendResult(
            cursor=EventCursor(event.store_seq, event.event_id),
            event=event,
        )

    def read(self, *, after=None, limit=100):
        start = after.store_seq if after is not None else 0
        return JournalPage(
            events=tuple(self.events[start : start + limit]),
            has_more=False,
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


class _ArtifactRepository(BoundArtifactRepository):
    def __init__(self, execution_id):
        super().__init__(execution_id)
        self.content = {}
        self.operations = {}

    def put(self, *, content, media_type, operation, preview=""):
        previous = self.operations.get(operation.operation_id)
        if previous is not None:
            prior_operation, artifact = previous
            if prior_operation != operation:
                raise RuntimeError("artifact operation conflict")
            return artifact
        digest = hashlib.sha256(content).hexdigest()
        artifact = ArtifactRef(
            ref=ResourceRef("artifact", f"object-{digest}", 1),
            media_type=media_type,
            byte_length=len(content),
            sha256=digest,
            preview=preview,
        )
        self.operations[operation.operation_id] = (operation, artifact)
        self.content[artifact.ref.resource_id] = content
        return artifact

    def read_verified(self, *, artifact, offset=0, limit=65_536):
        return self.content[artifact.ref.resource_id][offset : offset + limit]

    def read_full_verified(self, *, artifact):
        return self.content[artifact.ref.resource_id]


class _CheckpointRepository:
    def __init__(self, execution_id):
        self.execution_id = execution_id

    def prepare(self, **kwargs):
        raise AssertionError(kwargs)

    def commit(self, **kwargs):
        raise AssertionError(kwargs)

    def get_by_operation(self, **kwargs):
        return None


class _BuildRepository:
    def __init__(self, execution_id):
        self.execution_id = execution_id

    def record(self, **kwargs):
        raise AssertionError(kwargs)

    def get_by_operation(self, **kwargs):
        return None

    def get_by_trigger(self, **kwargs):
        return None


def _bundle(attempt, *, journal=None):
    journal = journal or _Journal(attempt.generation.execution_id)
    artifacts = ArtifactService(
        _ArtifactRepository(attempt.generation.execution_id),
        sanitizer=lambda content, media_type: content,
    )
    projector = CanonicalSemanticEventProjector(
        attempt=attempt,
        artifacts=artifacts,
        payload_sanitizer=lambda event_type, payload: payload,
    )
    sink = DurableEventSink(journal, attempt, projector)
    coordinator = ContextCompileCoordinator(
        journal=journal,
        checkpoint_repository=_CheckpointRepository(attempt.generation.execution_id),
        build_repository=_BuildRepository(attempt.generation.execution_id),
        partial_attempt_sink=lambda request, error: None,
    )
    handoffs = HandoffService(artifacts)
    return ContextExecutionBundle(
        attempt=attempt,
        journal=journal,
        projector=projector,
        durable_event_sink=sink,
        coordinator=coordinator,
        artifacts=artifacts,
        handoffs=handoffs,
        ingress=ContextInputIngress(
            attempt=attempt,
            projector=projector,
            sink=sink,
        ),
        request_factory=JournalContextRequestFactory(
            attempt=attempt,
            journal=journal,
            model_window_fallback=lambda provider, model: 8_192,
        ),
        tool_boundary=DurableToolBoundary(
            attempt=attempt,
            projector=projector,
            sink=sink,
        ),
        handoff_recorder=DurableHandoffRecorder(
            attempt=attempt,
            handoffs=handoffs,
            projector=projector,
            sink=sink,
        ),
        partial_attempt_sink=lambda event, error: None,
    )


def _context(*, session_id, run_id, current_input="current input"):
    state = RunState()
    state.session_state.session_id = session_id
    return HarnessContext(
        state=state,
        phase="bootstrap",
        event={"run_id": run_id, "current_input": current_input},
    )


def _current_input(context, attempt):
    content = context.event.get("current_input")
    if content is None:
        return None
    return HostResolvedCurrentInput(
        attempt=attempt,
        content=content,
    )


def test_shadow_context_does_not_replace_explicit_provider_accounting_owner(
    tmp_path,
) -> None:
    execution_id = "execution-shadow-owner"
    run_id = "run-shadow-owner"
    identity = RunIdentity(
        execution_id=execution_id,
        attempt_id=run_id,
        root_run_id=run_id,
        run_id=run_id,
        parent_run_id=None,
        relation="root",
    )
    generic_store = SQLiteContextV2Store(
        database_path=tmp_path / "generic" / "context.sqlite3",
        object_directory=tmp_path / "generic" / "objects",
    ).bind_execution(execution_id)
    shadow_store = SQLiteContextV2Store(
        database_path=tmp_path / "shadow" / "context.sqlite3",
        object_directory=tmp_path / "shadow" / "objects",
    ).bind_execution(execution_id)
    attempt = AttemptRef(
        generation=GenerationRef(execution_id, "generation-generic-owner"),
        attempt_id=run_id,
    )
    from unchain.context.provider_execution import (
        ContextProviderTurnExecutionService,
        official_provider_transport_target_sha256,
    )

    service = ContextProviderTurnExecutionService(
        attempt=attempt,
        store=generic_store,
        mode=DurableProviderTurnMode.ENFORCE_TEST,
        transport_target_sha256=official_provider_transport_target_sha256(),
        sleep=lambda _seconds: None,
    )
    owner = ProviderTurnOwnership(
        identity=identity,
        service=service,
        ledger=generic_store,
    )
    state = RunState()
    state.session_state.session_id = execution_id
    state.run_ledger.initialize(
        state=state,
        run_id=run_id,
        explicit_identity=identity,
    )
    state.run_ledger.bind_provider_turn_ownership(owner)

    factory = DurableContextRuntimeFactory(
        bundle_builder=lambda shadow_attempt: _bundle(
            shadow_attempt,
            journal=shadow_store,
        ),
        generation_resolver=lambda _context, _execution_id: "generation-shadow",
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-shadow",
        execution_factory=factory,
        provider_turns_enabled=False,
    )
    runtime.bind_context(
        HarnessContext(
            state=state,
            phase="bootstrap",
            event={"run_id": run_id, "current_input": "hello"},
        )
    )

    assert state.run_ledger.provider_turn_ownership is owner
    assert state.run_ledger.persistence is generic_store


def test_factory_runtime_routes_two_executions_to_distinct_durable_bundles() -> None:
    bundles = {}

    def build(attempt):
        bundle = _bundle(attempt)
        bundles[(attempt.generation.execution_id, attempt.attempt_id)] = bundle
        return bundle

    factory = DurableContextRuntimeFactory(
        bundle_builder=build,
        generation_resolver=lambda context, execution_id: (
            f"generation-{execution_id}"
        ),
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=factory,
    )

    runtime.bind_context(_context(session_id="session-a", run_id="run-a"))
    runtime.bind_context(_context(session_id="session-b", run_id="run-b"))
    runtime.persist_event({"type": "run_started", "run_id": "run-a", "iteration": 0})
    runtime.persist_event({"type": "run_started", "run_id": "run-b", "iteration": 0})

    assert [
        event.event_type for event in bundles[("session-a", "run-a")].journal.events
    ] == [
        "message.user",
        "run_started",
    ]
    assert [
        event.event_type for event in bundles[("session-b", "run-b")].journal.events
    ] == [
        "message.user",
        "run_started",
    ]
    assert runtime.build_harnesses()[0].name == "context_v2_execution_binding"
    assert runtime.build_harnesses()[1].name == "context_v2_tool_authority"
    assert runtime.build_harnesses()[2].name == "context_v2_compiler"


def test_factory_binds_one_attempt_scoped_tool_output_manager_to_event() -> None:
    factory = DurableContextRuntimeFactory(
        bundle_builder=_bundle,
        generation_resolver=lambda context, execution_id: "generation-1",
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=factory,
    )
    context = _context(session_id="session-output", run_id="run-output")
    context.event["tool_runtime_config"] = {
        "tool_output_management": ToolOutputManager.active_default(
            attempt_id="run-output"
        ).runtime_snapshot()
    }

    runtime.bind_context(context)
    manager = context.event["tool_output_manager"]

    assert manager is factory.bind(context).tool_output_manager
    assert manager.active is True
    assert manager.legacy_budget_enabled is False


def test_factory_binds_the_exact_closed_tool_output_snapshot() -> None:
    factory = DurableContextRuntimeFactory(
        bundle_builder=_bundle,
        generation_resolver=lambda context, execution_id: "generation-1",
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=factory,
    )
    context = _context(session_id="session-snapshot", run_id="run-snapshot")
    configured = ToolOutputManager.active_default(attempt_id="run-snapshot")
    snapshot = configured.runtime_snapshot()
    snapshot["policies"] = [
        policy for policy in snapshot["policies"] if policy["name"] == "default"
    ]
    context.event["tool_runtime_config"] = {
        "tool_output_management": snapshot,
    }

    runtime.bind_context(context)
    manager = context.event["tool_output_manager"]

    assert manager.runtime_snapshot() == snapshot
    with pytest.raises(ToolOutputPolicyVersionError):
        manager.resolve_policy("head_tail")


def test_factory_closes_tool_output_when_no_snapshot_is_supplied() -> None:
    factory = DurableContextRuntimeFactory(
        bundle_builder=_bundle,
        generation_resolver=lambda context, execution_id: "generation-1",
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=factory,
    )
    context = _context(session_id="session-no-snapshot", run_id="run-no-snapshot")

    runtime.bind_context(context)

    manager = context.event["tool_output_manager"]
    assert manager.active is False
    assert manager.legacy_budget_enabled is True


def test_active_factory_derives_tool_output_snapshot_from_exposed_toolkit() -> None:
    factory = DurableContextRuntimeFactory(
        bundle_builder=_bundle,
        generation_resolver=lambda context, execution_id: "generation-1",
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=factory,
        tool_output_management_active=True,
    )
    tool = Tool(
        name="large_search",
        description="search",
        output_policy="artifact_only",
    )
    context = _context(session_id="session-active", run_id="run-active")
    context.event["toolkit"] = Toolkit({"large_search": tool})

    runtime.bind_context(context)
    assert context.event["tool_output_manager"].active is False

    runtime.bind_execution_toolkit(context)

    assert context.event["tool_output_manager"].active is True
    assert context.event["tool_runtime_config"]["tool_output_policy_map"] == {
        "schema": "unchain.tool_output_policy_map.v1",
        "policies": {"large_search": "artifact_only"},
    }

    context.event["toolkit"] = Toolkit(
        {
            "large_search": Tool(
                name="large_search",
                description="search",
                output_policy="default",
            )
        }
    )
    with pytest.raises(
        ContextExecutionBundleError,
        match="frozen tool output policy map",
    ):
        runtime.bind_execution_toolkit(context)


def test_active_factory_reuses_frozen_snapshot_on_repeated_bootstrap() -> None:
    factory = DurableContextRuntimeFactory(
        bundle_builder=_bundle,
        generation_resolver=lambda context, execution_id: "generation-1",
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=factory,
        tool_output_management_active=True,
    )

    def context_for_same_attempt():
        context = _context(session_id="session-repeat", run_id="run-repeat")
        context.event["tool_runtime_config"] = {"memory_v2_context": {"mode": "active"}}
        context.event["toolkit"] = Toolkit(
            {
                "search": Tool(
                    name="search",
                    description="search",
                    output_policy="artifact_only",
                )
            }
        )
        return context

    first = context_for_same_attempt()
    runtime.bind_context(first)
    runtime.bind_execution_toolkit(first)
    second = context_for_same_attempt()

    runtime.bind_context(second)

    assert second.event["tool_output_manager"].active is True
    assert second.event["tool_runtime_config"]["tool_output_policy_map"] == {
        "schema": "unchain.tool_output_policy_map.v1",
        "policies": {"search": "artifact_only"},
    }


def test_active_factory_cold_restart_rejects_changed_attempt_policy_snapshot() -> None:
    journals = {}

    def factory_for_cold_runtime():
        return DurableContextRuntimeFactory(
            bundle_builder=lambda attempt: _bundle(
                attempt,
                journal=journals.setdefault(
                    attempt.generation.execution_id,
                    _Journal(attempt.generation.execution_id),
                ),
            ),
            generation_resolver=lambda _context, execution_id: f"generation-{execution_id}",
            current_input_resolver=_current_input,
        )

    first_runtime = ContextRuntime.from_factory(
        owner_id="context-v2-first",
        execution_factory=factory_for_cold_runtime(),
        tool_output_management_active=True,
    )
    first = _context(session_id="session-cold-policy", run_id="run-cold-policy")
    first.event["toolkit"] = Toolkit(
        {
            "search": Tool(
                name="search",
                description="search",
                output_policy="artifact_only",
            )
        }
    )
    first_runtime.bind_context(first)
    first_runtime.bind_execution_toolkit(first)

    cold_runtime = ContextRuntime.from_factory(
        owner_id="context-v2-cold",
        execution_factory=factory_for_cold_runtime(),
        tool_output_management_active=True,
    )
    resumed = _context(session_id="session-cold-policy", run_id="run-cold-policy")
    resumed.event["toolkit"] = Toolkit(
        {
            "search": Tool(
                name="search",
                description="search",
                output_policy="head_tail",
            )
        }
    )
    cold_runtime.bind_context(resumed)

    with pytest.raises(
        ContextExecutionBundleError,
        match="durable tool output policy map",
    ):
        cold_runtime.bind_execution_toolkit(resumed)


def test_factory_rejects_a_changed_tool_output_snapshot_after_binding() -> None:
    factory = DurableContextRuntimeFactory(
        bundle_builder=_bundle,
        generation_resolver=lambda context, execution_id: "generation-1",
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=factory,
    )
    first = _context(session_id="session-fixed", run_id="run-fixed")
    configured = ToolOutputManager.active_default(attempt_id="run-fixed")
    fixed_snapshot = configured.runtime_snapshot()
    fixed_snapshot["policies"] = [
        policy for policy in fixed_snapshot["policies"] if policy["name"] == "default"
    ]
    first.event["tool_runtime_config"] = {
        "tool_output_management": fixed_snapshot,
    }
    runtime.bind_context(first)

    changed = _context(session_id="session-fixed", run_id="run-fixed")
    changed.event["tool_runtime_config"] = {
        "tool_output_management": ToolOutputManager.active_default(
            attempt_id="run-fixed"
        ).runtime_snapshot(),
    }

    with pytest.raises(ContextExecutionBundleError, match="snapshot changed"):
        runtime.bind_context(changed)
    assert factory.bind(first).tool_output_manager.runtime_snapshot() == fixed_snapshot


def test_bundle_rejects_sink_and_coordinator_with_different_journals() -> None:
    from unchain.journal import AttemptRef, GenerationRef

    attempt = AttemptRef(GenerationRef("execution-1", "generation-1"), "run-1")
    valid = _bundle(attempt)

    with pytest.raises(ContextExecutionBundleError, match="same journal"):
        ContextExecutionBundle(
            attempt=attempt,
            journal=valid.journal,
            projector=valid.projector,
            durable_event_sink=DurableEventSink(
                _Journal("execution-1"),
                attempt,
                valid.projector,
            ),
            coordinator=valid.coordinator,
            artifacts=valid.artifacts,
            handoffs=valid.handoffs,
            ingress=valid.ingress,
            request_factory=valid.request_factory,
            tool_boundary=valid.tool_boundary,
            handoff_recorder=valid.handoff_recorder,
            partial_attempt_sink=valid.partial_attempt_sink,
        )


def test_factory_runtime_rejects_event_before_bootstrap_binding() -> None:
    factory = DurableContextRuntimeFactory(
        bundle_builder=_bundle,
        generation_resolver=lambda context, execution_id: "generation-1",
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=factory,
    )

    with pytest.raises(ContextExecutionBundleError, match="bootstrap"):
        runtime.persist_event(
            {"type": "run_started", "run_id": "run-unbound", "iteration": 0}
        )


def test_bundle_rejects_arbitrary_request_factory_and_missing_official_services() -> (
    None
):
    from dataclasses import replace
    from unchain.journal import AttemptRef, GenerationRef

    attempt = AttemptRef(GenerationRef("execution-1", "generation-1"), "run-1")
    valid = _bundle(attempt)

    with pytest.raises(TypeError, match="JournalContextRequestFactory"):
        replace(valid, request_factory=lambda context: None)
    with pytest.raises(TypeError, match="ContextInputIngress"):
        replace(valid, ingress=None)
    with pytest.raises(TypeError, match="DurableToolBoundary"):
        replace(valid, tool_boundary=None)
    with pytest.raises(TypeError, match="DurableHandoffRecorder"):
        replace(valid, handoff_recorder=None)


def test_factory_caches_one_bundle_for_repeated_bootstrap() -> None:
    build_calls = []

    def build(attempt):
        build_calls.append(attempt)
        return _bundle(attempt)

    factory = DurableContextRuntimeFactory(
        bundle_builder=build,
        generation_resolver=lambda context, execution_id: "generation-1",
        current_input_resolver=_current_input,
    )
    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=factory,
    )
    context = _context(session_id="execution-1", run_id="run-1")

    runtime.bind_context(context)
    runtime.bind_context(context)

    assert len(build_calls) == 1


def test_factory_bootstrap_accepts_explicit_host_interaction_input() -> None:
    bundles = {}

    def build(attempt):
        bundle = _bundle(attempt)
        paused_attempt = AttemptRef(attempt.generation, "run-paused")
        bundle.journal.append(
            request=SemanticEventDraft(
                event_id="event-interaction-requested",
                event_type="interaction.requested",
                attempt=paused_attempt,
                operation_id="operation-interaction-requested",
                payload={
                    "run_id": paused_attempt.attempt_id,
                    "interaction_id": "interaction-human-1",
                },
            ).to_append_request()
        )
        bundles[attempt.attempt_id] = bundle
        return bundle

    def resolve_interaction(context, attempt):
        return HostResolvedInteractionInput(
            attempt=attempt,
            interaction_id=context.event["interaction_id"],
            response=context.event["interaction_response"],
            submitted_by="ui:test",
        )

    runtime = ContextRuntime.from_factory(
        owner_id="context-v2",
        execution_factory=DurableContextRuntimeFactory(
            bundle_builder=build,
            generation_resolver=lambda context, execution_id: "generation-1",
            current_input_resolver=resolve_interaction,
        ),
    )
    context = _context(
        session_id="execution-1",
        run_id="run-resumed",
        current_input=None,
    )
    context.event.update(
        interaction_id="interaction-human-1",
        interaction_response={"selected_values": ["react"]},
    )

    runtime.bind_context(context)

    assert [event.event_type for event in bundles["run-resumed"].journal.events] == [
        "interaction.requested",
        "interaction.resolved",
    ]
