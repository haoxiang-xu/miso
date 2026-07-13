from __future__ import annotations

import copy
import uuid
from contextlib import nullcontext
from functools import partial
from typing import Any, Callable

from ..execution import (
    ExecutionGuard,
    ExecutionLeaseNotOwnedError,
    ExecutionRuntime,
)
from ..interaction.runtime import (
    DurableInteractionRuntime,
    require_unapplied_callback_receipt,
)
from ..interaction.adapters import DurableMaxBudgetCallbackAdapter
from ..interaction.durable import (
    INTERACTION_KIND_HUMAN_INPUT,
    INTERACTION_KIND_MAX_BUDGET,
    INTERACTION_KIND_TOOL_APPROVAL,
    InteractionIntegrityError,
    InteractionNotPendingError,
)
from ..interaction.requests import ensure_interaction_runtime_matches
from ..interaction.effects import (
    HUMAN_INPUT_CONTINUATION_TYPE,
)
from ..providers.model_turn_runtime import apply_model_turn_result, fetch_model_turn
from ..retry import RetryConfig
from ..schemas import ResponseFormat
from ..tools.toolkit import Toolkit
from ..tools.models import ToolConfirmationRequest
from ..tools.runtime import snapshot_durable_tool_exposure_plan
from ..tools.types import ToolBatchState
from .delta import HarnessDelta
from .harness import HarnessContext, RuntimeHarness, RuntimePhase
from .lifecycle_events import (
    build_iteration_completed_payload,
    build_iteration_started_payload,
    build_response_received_payload,
    build_run_started_payload,
)
from .model_io import ModelIO
from .provider_replay import set_provider_replay_frame
from .run_limits import resolve_max_iterations_boundary
from .run_outcomes import (
    finish_awaiting_interaction_run,
    finish_awaiting_human_input_run,
    finish_completed_run,
    finish_max_iterations_run,
)
from .run_preparation import (
    infer_model,
    infer_provider,
    prepare_fresh_run_invocation,
    prepare_resume_run_invocation,
    prepare_state_for_execution,
)
from .state import RunState
from .types import KernelRunResult, ModelTurnResult, ToolCall


_DURABLE_BARRIER_PHASES = frozenset({"suspend_persist", "finalize_persist"})


class KernelLoop:
    """Minimal harness-driven loop skeleton for the new kernel."""

    def __init__(
        self,
        *,
        harnesses: list[RuntimeHarness] | None = None,
        model_io: ModelIO | None = None,
        retry_config: RetryConfig | None = None,
        execution_runtime: ExecutionRuntime | None = None,
        interaction_runtime: DurableInteractionRuntime | None = None,
    ) -> None:
        self._harnesses: list[RuntimeHarness] = []
        self._model_io = model_io
        self._retry_config: RetryConfig = retry_config if retry_config is not None else RetryConfig()
        self._execution_runtime = execution_runtime
        self._interaction_runtime = interaction_runtime
        for harness in harnesses or []:
            self.register_harness(harness)

    @property
    def harnesses(self) -> list[RuntimeHarness]:
        return list(self._harnesses)

    def register_harness(self, harness: RuntimeHarness) -> None:
        harness_phases = set(getattr(harness, "phases", ()))
        reserved_phases = harness_phases & _DURABLE_BARRIER_PHASES
        if reserved_phases and getattr(harness, "durable_barrier", False) is not True:
            phase_list = ", ".join(sorted(reserved_phases))
            raise ValueError(
                f"harness '{harness.name}' cannot register reserved durable "
                f"phase(s): {phase_list}"
            )
        for phase in reserved_phases:
            existing = next(
                (
                    item
                    for item in self._harnesses
                    if phase in getattr(item, "phases", ())
                ),
                None,
            )
            if existing is not None:
                raise ValueError(
                    f"durable phase '{phase}' already belongs to harness "
                    f"'{existing.name}'"
                )
        self._harnesses.append(harness)
        self._harnesses.sort(key=lambda item: (item.order, item.name))

    def register_context_optimizer(self, optimizer: RuntimeHarness) -> None:
        self.register_harness(optimizer)

    @property
    def model_io(self) -> ModelIO | None:
        return self._model_io

    @property
    def execution_runtime(self) -> ExecutionRuntime | None:
        return self._execution_runtime

    @property
    def interaction_runtime(self) -> DurableInteractionRuntime | None:
        return self._interaction_runtime

    @interaction_runtime.setter
    def interaction_runtime(self, value: DurableInteractionRuntime | None) -> None:
        self._interaction_runtime = value

    @model_io.setter
    def model_io(self, value: ModelIO | None) -> None:
        self._model_io = value

    def _validate_execution_guard(
        self,
        state: RunState,
        execution_guard: ExecutionGuard | None,
    ) -> ExecutionGuard | None:
        session_id = str(state.session_state.session_id or "")
        if execution_guard is None:
            if self._execution_runtime is not None and session_id:
                raise ExecutionLeaseNotOwnedError(
                    "execution-lease-enabled KernelLoop requires an active guard",
                    execution_id=session_id,
                )
            return None
        if session_id and execution_guard.lease.execution_id != session_id:
            raise ValueError(
                "execution guard does not belong to the RunState session_id"
            )
        execution_guard.assert_active()
        return execution_guard

    def _scope_for_session(
        self,
        *,
        session_id: str | None,
        execution_guard: ExecutionGuard | None,
    ):
        normalized_session_id = str(session_id or "")
        if execution_guard is not None:
            if (
                normalized_session_id
                and execution_guard.lease.execution_id != normalized_session_id
            ):
                raise ValueError(
                    "execution guard does not belong to the requested session_id"
                )
            execution_guard.assert_active()
            return nullcontext(execution_guard)
        if self._execution_runtime is not None and normalized_session_id:
            return self._execution_runtime.scope(normalized_session_id)
        return nullcontext(None)

    def seed_state(
        self,
        messages: list[dict[str, Any]],
        *,
        provider: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        max_context_window_tokens: int | None = None,
    ) -> RunState:
        state = RunState()
        state.seed_messages(messages)
        state.provider_state.provider = provider
        state.provider_state.model = model
        state.provider_state.max_context_window_tokens = max(0, int(max_context_window_tokens or 0))
        state.session_state.session_id = session_id
        state.session_state.memory_namespace = memory_namespace
        return state

    def dispatch_phase(
        self,
        state: RunState,
        *,
        phase: RuntimePhase,
        event: dict[str, Any] | None = None,
    ) -> RunState:
        context = HarnessContext(state=state, phase=phase, event=event or {})
        for harness in self._iter_phase_harnesses(phase):
            if not harness.applies(context):
                continue
            apply = getattr(harness, "apply", None)
            raw_outcome = apply(context) if callable(apply) else harness.build_delta(context)
            if raw_outcome is None:
                continue

            from ..capabilities import normalize_capability_outcome
            from .application import apply_run_delta

            outcome = normalize_capability_outcome(
                raw_outcome,
                created_by=f"harness.{harness.name}",
            )
            delta = outcome.delta
            if delta is None:
                continue
            if not isinstance(delta, HarnessDelta) and not getattr(delta, "context_ops", ()):
                raise TypeError(
                    f"harness '{harness.name}' returned {type(delta).__name__}, expected RunDelta"
                )

            def emit_structured_event(op):
                payload = op.payload if isinstance(op.payload, dict) else {}
                raw_iteration = (event or {}).get("iteration", state.iteration)
                try:
                    iteration = int(raw_iteration)
                except (TypeError, ValueError):
                    iteration = int(state.iteration)
                self.emit_event(
                    (event or {}).get("callback"),
                    op.type,
                    str((event or {}).get("run_id") or "kernel"),
                    iteration=iteration,
                    **copy.deepcopy(payload),
                )

            apply_run_delta(state, delta, emit_event=emit_structured_event)
            context = HarnessContext(state=state, phase=phase, event=event or {})
        return state

    def fetch_model_turn(
        self,
        state: RunState,
        *,
        payload: dict[str, Any] | None = None,
        toolkit: Toolkit | None = None,
        callback: Any = None,
        verbose: bool = False,
        run_id: str = "kernel",
        emit_stream: bool = False,
        response_format: Any = None,
        openai_text_format: dict[str, Any] | None = None,
        execution_guard: ExecutionGuard | None = None,
    ):
        return fetch_model_turn(
            model_io=self._model_io,
            retry_config=self._retry_config,
            state=state,
            payload=payload,
            toolkit=toolkit,
            callback=callback,
            verbose=verbose,
            run_id=run_id,
            emit_stream=emit_stream,
            response_format=response_format,
            openai_text_format=openai_text_format,
            before_attempt=(
                (lambda _attempt: execution_guard.renew())
                if execution_guard is not None
                else None
            ),
        )

    def apply_model_turn(
        self,
        state: RunState,
        turn: ModelTurnResult,
        *,
        created_by: str = "kernel.model_turn",
    ) -> RunState:
        return apply_model_turn_result(state, turn, created_by=created_by)

    def step_once(
        self,
        state: RunState,
        *,
        payload: dict[str, Any] | None = None,
        toolkit: Toolkit | None = None,
        callback: Any = None,
        verbose: bool = False,
        run_id: str = "kernel",
        emit_stream: bool = False,
        response_format: Any = None,
        openai_text_format: dict[str, Any] | None = None,
        on_tool_confirm: Any = None,
        on_human_input: Any = None,
        max_iterations: int = 0,
        tool_runtime_plugins: list[Any] | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
        execution_guard: ExecutionGuard | None = None,
    ) -> ModelTurnResult:
        execution_guard = self._validate_execution_guard(state, execution_guard)
        runtime_toolkit = toolkit if toolkit is not None else Toolkit()
        current_iteration = int(state.iteration)
        local_next_context = (
            state.next_model_input
            if isinstance(state.next_model_input, list)
            else state.transcript
        )
        state.rebuild_working_version(
            local_next_context,
            created_by="kernel.model_context_snapshot",
            metadata={
                "iteration": current_iteration,
                "transcript_message_count": len(state.transcript),
                "source": (
                    "next_model_input"
                    if local_next_context is state.next_model_input
                    else "transcript"
                ),
            },
        )
        phase_event = {
            "payload": dict(payload or {}),
            "toolkit": runtime_toolkit,
            "callback": callback,
            "verbose": verbose,
            "run_id": run_id,
            "emit_stream": emit_stream,
            "response_format": response_format,
            "openai_text_format": openai_text_format,
            "on_tool_confirm": on_tool_confirm,
            "on_human_input": on_human_input,
            "max_iterations": max_iterations,
            "supports_tools": True,
            "loop": self,
            "model_io": self._model_io,
            "tool_runtime_plugins": list(tool_runtime_plugins or []),
            "tool_runtime_config": copy.deepcopy(tool_runtime_config or {}),
            "execution_guard": execution_guard,
        }

        if execution_guard is not None:
            execution_guard.renew()
        self.dispatch_phase(state, phase="before_model", event=phase_event)
        if execution_guard is not None:
            execution_guard.assert_active()
        turn = self.fetch_model_turn(
            state,
            payload=payload,
            toolkit=runtime_toolkit,
            callback=callback,
            verbose=verbose,
            run_id=run_id,
            emit_stream=emit_stream,
            response_format=response_format,
            openai_text_format=openai_text_format,
            execution_guard=execution_guard,
        )
        if execution_guard is not None:
            execution_guard.assert_active()
        self.apply_model_turn(state, turn)

        after_model_event = {
            **phase_event,
            "turn_result": turn,
        }
        self.dispatch_phase(state, phase="after_model", event=after_model_event)
        if execution_guard is not None:
            execution_guard.assert_active()

        tool_calls = list(state.pending_tool_calls)
        if tool_calls:
            for index, tool_call in enumerate(tool_calls):
                if execution_guard is not None:
                    execution_guard.renew()
                self.dispatch_phase(
                    state,
                    phase="on_tool_call",
                    event={
                        **after_model_event,
                        "tool_call": tool_call,
                        "tool_call_index": index,
                        "tool_calls": tool_calls,
                    },
                )
                if execution_guard is not None:
                    execution_guard.assert_active()
            self.dispatch_phase(
                state,
                phase="after_tool_batch",
                event={
                    **after_model_event,
                    "tool_calls": tool_calls,
                },
            )
            if execution_guard is not None:
                execution_guard.assert_active()
        else:
            if execution_guard is not None:
                execution_guard.renew()
            self.dispatch_phase(state, phase="before_commit", event=after_model_event)
            if execution_guard is not None:
                execution_guard.assert_active()

        state.iteration = current_iteration + 1
        return turn

    def _iter_phase_harnesses(self, phase: RuntimePhase) -> list[RuntimeHarness]:
        return [harness for harness in self._harnesses if phase in harness.phases]

    def _dispatch_bootstrap(
        self,
        state: RunState,
        *,
        payload: dict[str, Any] | None,
        response_format: ResponseFormat | None,
        callback: Any,
        verbose: bool,
        toolkit: Toolkit | None,
        run_id: str,
        resume_mode: bool,
        continuation: dict[str, Any] | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
        execution_guard: ExecutionGuard | None = None,
    ) -> None:
        runtime_toolkit = toolkit if toolkit is not None else Toolkit()
        if execution_guard is not None:
            execution_guard.renew()
        self.dispatch_phase(
            state,
            phase="bootstrap",
            event={
                "payload": dict(payload or {}),
                "toolkit": runtime_toolkit,
                "callback": callback,
                "verbose": verbose,
                "run_id": run_id,
                "response_format": response_format,
                "supports_tools": True,
                "resume_mode": resume_mode,
                "continuation": copy.deepcopy(continuation),
                "loop": self,
                "tool_runtime_config": copy.deepcopy(tool_runtime_config or {}),
                "execution_guard": execution_guard,
            },
        )
        if execution_guard is not None:
            execution_guard.assert_active()

    def _dispatch_run_finalizing(
        self,
        state: RunState,
        *,
        callback: Any,
        run_id: str,
        iteration: int,
        status: str,
        execution_guard: ExecutionGuard | None = None,
    ) -> None:
        event = {
            "callback": callback,
            "run_id": run_id,
            "iteration": int(iteration),
            "status": status,
            "loop": self,
            "execution_guard": execution_guard,
        }
        if execution_guard is not None:
            execution_guard.renew()
        self.dispatch_phase(state, phase="run_finalizing", event=event)
        if execution_guard is not None:
            execution_guard.assert_active()
        self.dispatch_phase(state, phase="finalize_persist", event=event)
        if execution_guard is not None:
            execution_guard.assert_active()

    def _dispatch_on_suspend(
        self,
        state: RunState,
        *,
        callback: Any,
        run_id: str,
        iteration: int,
        status: str,
        execution_guard: ExecutionGuard | None = None,
    ) -> None:
        event = {
            "callback": callback,
            "run_id": run_id,
            "iteration": int(iteration),
            "status": status,
            "loop": self,
            "execution_guard": execution_guard,
        }
        if execution_guard is not None:
            execution_guard.renew()
        self.dispatch_phase(state, phase="on_suspend", event=event)
        if execution_guard is not None:
            execution_guard.assert_active()
        self.dispatch_phase(state, phase="suspend_persist", event=event)
        if execution_guard is not None:
            execution_guard.assert_active()

    def _dispatch_on_resume(
        self,
        state: RunState,
        *,
        continuation: dict[str, Any],
        response: Any,
        callback: Any,
        run_id: str,
        execution_guard: ExecutionGuard | None = None,
    ) -> None:
        if execution_guard is not None:
            execution_guard.renew()
        self.dispatch_phase(
            state,
            phase="on_resume",
            event={
                "continuation": copy.deepcopy(continuation),
                "response": copy.deepcopy(response),
                "callback": callback,
                "run_id": run_id,
                "iteration": int(state.iteration),
                "loop": self,
                "execution_guard": execution_guard,
            },
        )
        if execution_guard is not None:
            execution_guard.assert_active()

    def emit_event(
        self,
        callback: Any,
        event_type: str,
        run_id: str,
        *,
        iteration: int,
        **extra: Any,
    ) -> None:
        if callback is None:
            return
        event = {
            "type": event_type,
            "run_id": run_id,
            "iteration": iteration,
        }
        event.update(copy.deepcopy(extra))
        callback(event)

    def _infer_provider(self) -> str | None:
        return infer_provider(self._model_io)

    def _infer_model(self) -> str | None:
        return infer_model(self._model_io)

    @staticmethod
    def _wrap_max_iterations_callback(
        callback: Any,
        *,
        execution_guard: ExecutionGuard | None,
        expected_revision: Callable[[], int | None],
    ) -> Any:
        if execution_guard is None or not callable(callback):
            return callback

        def guarded_callback(decision: Any) -> Any:
            response = callback(decision)
            revision = expected_revision()
            if revision is not None:
                execution_guard.reacquire(expected_revision=revision)
            else:
                execution_guard.assert_active()
            return response

        return guarded_callback

    @staticmethod
    def _guard_terminal_emitter(
        emit_event: Callable[..., None],
        *,
        execution_guard: ExecutionGuard | None,
    ) -> Callable[..., None]:
        if execution_guard is None:
            return emit_event

        def guarded_emit(*args: Any, **kwargs: Any) -> None:
            execution_guard.assert_active()
            emit_event(*args, **kwargs)
            execution_guard.assert_active()

        return guarded_emit

    @staticmethod
    def _tool_confirmation_request_from_interaction(
        interaction_request: dict[str, Any],
    ) -> ToolConfirmationRequest:
        raw = interaction_request.get("payload")
        if not isinstance(raw, dict):
            raise InteractionIntegrityError(
                "tool approval interaction payload must be an object"
            )
        arguments = raw.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        interact_config = raw.get("interact_config")
        if not isinstance(interact_config, (dict, list)):
            interact_config = None
        render_component = raw.get("render_component")
        if not isinstance(render_component, dict):
            render_component = None
        return ToolConfirmationRequest(
            tool_name=str(raw.get("tool_name") or ""),
            call_id=str(raw.get("call_id") or ""),
            arguments=copy.deepcopy(arguments),
            description=str(raw.get("description") or ""),
            interact_type=str(raw.get("interact_type") or "confirmation"),
            interact_config=copy.deepcopy(interact_config),
            render_component=copy.deepcopy(render_component),
        )

    def _dispatch_tool_approval_resume(
        self,
        state: RunState,
        *,
        continuation: dict[str, Any],
        interaction_request: dict[str, Any],
        response: dict[str, Any],
        payload: dict[str, Any] | None,
        response_format: ResponseFormat | None,
        callback: Any,
        verbose: bool,
        on_tool_confirm: Any,
        on_human_input: Any,
        max_iterations: int,
        toolkit: Toolkit,
        run_id: str,
        tool_runtime_plugins: list[Any] | None,
        tool_runtime_config: dict[str, Any] | None,
        execution_guard: ExecutionGuard | None,
    ) -> None:
        if continuation.get("type") != "tool_approval_continuation":
            raise InteractionIntegrityError(
                "tool approval receipt requires a tool_approval_continuation"
            )
        raw_tool_calls = continuation.get("tool_calls")
        if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
            raise InteractionIntegrityError(
                "tool approval continuation requires pending tool calls"
            )
        tool_calls: list[ToolCall] = []
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, dict):
                raise InteractionIntegrityError(
                    "tool approval continuation contains an invalid tool call"
                )
            call_id = str(raw_call.get("call_id") or "")
            name = str(raw_call.get("name") or "")
            if not call_id or not name:
                raise InteractionIntegrityError(
                    "tool approval continuation tool call requires id and name"
                )
            tool_calls.append(
                ToolCall(
                    call_id=call_id,
                    name=name,
                    arguments=copy.deepcopy(raw_call.get("arguments")),
                )
            )
        raw_batch = continuation.get("tool_batch_state")
        if not isinstance(raw_batch, dict):
            raise InteractionIntegrityError(
                "tool approval continuation requires tool_batch_state"
            )
        result_messages = raw_batch.get("result_messages")
        executed_call_ids = raw_batch.get("executed_call_ids")
        if not isinstance(result_messages, list) or not isinstance(
            executed_call_ids, list
        ):
            raise InteractionIntegrityError(
                "tool approval continuation has invalid batch state"
            )
        state.pending_tool_calls = list(tool_calls)
        state.tool_batch_state = ToolBatchState(
            result_messages=copy.deepcopy(result_messages),
            should_observe=bool(raw_batch.get("should_observe", False)),
            executed_call_ids=[
                str(item) for item in executed_call_ids if isinstance(item, str)
            ],
        )
        state.component_bucket("tools")["tool_batch_state"] = (
            state.tool_batch_state.copy()
        )
        state.run_status = "running"
        state.last_continuation = None
        state.suspend_state.signal_kind = None
        state.suspend_state.payload = {}

        tool_iteration = int(
            continuation.get("tool_iteration", max(0, int(state.iteration) - 1))
        )
        next_iteration = int(continuation.get("iteration", tool_iteration + 1))
        state.iteration = tool_iteration
        phase_event = {
            "payload": dict(payload or {}),
            "toolkit": toolkit,
            "callback": callback,
            "verbose": verbose,
            "run_id": run_id,
            "emit_stream": True,
            "response_format": response_format,
            "on_tool_confirm": on_tool_confirm,
            "on_human_input": on_human_input,
            "max_iterations": max_iterations,
            "supports_tools": True,
            "loop": self,
            "model_io": self._model_io,
            "tool_runtime_plugins": list(tool_runtime_plugins or []),
            "tool_runtime_config": copy.deepcopy(tool_runtime_config or {}),
            "execution_guard": execution_guard,
            "turn_result": state.last_model_turn,
            "tool_calls": tool_calls,
        }
        paused_call_id = str(continuation.get("paused_call_id") or "")
        try:
            for index, tool_call in enumerate(tool_calls):
                if execution_guard is not None:
                    execution_guard.renew()
                resume_fields = (
                    {
                        "interaction_request": copy.deepcopy(
                            interaction_request
                        ),
                        "interaction_response": copy.deepcopy(response),
                    }
                    if tool_call.call_id == paused_call_id
                    else {}
                )
                self.dispatch_phase(
                    state,
                    phase="on_tool_call",
                    event={
                        **phase_event,
                        **resume_fields,
                        "tool_call": tool_call,
                        "tool_call_index": index,
                    },
                )
                if execution_guard is not None:
                    execution_guard.assert_active()
            self.dispatch_phase(
                state,
                phase="after_tool_batch",
                event=phase_event,
            )
            if execution_guard is not None:
                execution_guard.assert_active()
        finally:
            state.iteration = next_iteration

    def _run_state(
        self,
        state: RunState,
        *,
        payload: dict[str, Any] | None = None,
        response_format: ResponseFormat | None = None,
        callback: Any = None,
        verbose: bool = False,
        max_iterations: int = 6,
        on_tool_confirm: Any = None,
        on_human_input: Any = None,
        on_max_iterations: Any = None,
        toolkit: Toolkit | None = None,
        run_id: str | None = None,
        skip_bootstrap: bool = False,
        tool_runtime_plugins: list[Any] | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
        execution_guard: ExecutionGuard | None = None,
    ) -> KernelRunResult:
        if self._model_io is None:
            raise RuntimeError("KernelLoop.model_io is not configured")
        prepare_state_for_execution(state, model_io=self._model_io)
        execution_guard = self._validate_execution_guard(state, execution_guard)
        run_id = str(run_id or uuid.uuid4())
        runtime_toolkit = toolkit if toolkit is not None else Toolkit()
        if not skip_bootstrap:
            self._dispatch_bootstrap(
                state,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                toolkit=runtime_toolkit,
                run_id=run_id,
                resume_mode=False,
                tool_runtime_config=tool_runtime_config,
                execution_guard=execution_guard,
            )
        self.emit_event(
            callback,
            "run_started",
            run_id,
            **build_run_started_payload(state),
        )
        effective_max = int(max_iterations)
        if (
            state.memory_state.get("execution_checkpoint_restored") is True
            and state.memory_state.get("execution_checkpoint_status")
            == "max_iterations"
        ):
            # A fresh run that restores a max-iterations checkpoint treats its
            # limit as budget for this invocation.  ``state.iteration`` remains
            # cumulative so telemetry and checkpoint diagnostics stay honest.
            effective_max += int(state.iteration)
        terminal_emit_event = self._guard_terminal_emitter(
            self.emit_event,
            execution_guard=execution_guard,
        )
        while True:
            def current_session_revision() -> int | None:
                revision = state.memory_state.get("session_revision")
                if isinstance(revision, bool) or not isinstance(revision, int):
                    return None
                return revision

            dispatch_run_finalizing = partial(
                self._dispatch_run_finalizing,
                execution_guard=execution_guard,
            )
            dispatch_on_suspend = partial(
                self._dispatch_on_suspend,
                execution_guard=execution_guard,
            )

            max_wait_revision: int | None = None
            durable_max_wait = (
                callable(on_max_iterations)
                and self._interaction_runtime is not None
                and bool(state.session_state.session_id)
            )

            def persist_before_legacy_max_wait() -> None:
                nonlocal max_wait_revision
                state.run_status = "max_iterations"
                dispatch_on_suspend(
                    state,
                    callback=callback,
                    run_id=run_id,
                    iteration=int(state.iteration),
                    status="max_iterations",
                )
                max_wait_revision = current_session_revision()
                if execution_guard is not None and max_wait_revision is not None:
                    execution_guard.release_for_wait()

            if durable_max_wait:
                if self._interaction_runtime is None or not callable(
                    on_max_iterations
                ):
                    raise InteractionNotPendingError(
                        "max-budget interaction has no callback adapter"
                    )
                max_adapter = DurableMaxBudgetCallbackAdapter(
                    state=state,
                    interaction_runtime=self._interaction_runtime,
                    callback_adapter=on_max_iterations,
                    payload=dict(payload or {}),
                    response_format=response_format,
                    effective_max=effective_max,
                    run_id=run_id,
                    event_callback=callback,
                    emit_event=self.emit_event,
                    dispatch_on_suspend=dispatch_on_suspend,
                    current_session_revision=current_session_revision,
                    tool_exposure_plan_factory=(
                        lambda: snapshot_durable_tool_exposure_plan(
                            tool_runtime_plugins
                        )
                    ),
                    execution_guard=execution_guard,
                )
                boundary_callback = max_adapter.invoke
                before_max_wait = max_adapter.before_wait
            else:
                max_adapter = None
                boundary_callback = self._wrap_max_iterations_callback(
                    on_max_iterations,
                    execution_guard=execution_guard,
                    expected_revision=lambda: max_wait_revision,
                )
                before_max_wait = persist_before_legacy_max_wait

            boundary = resolve_max_iterations_boundary(
                state,
                effective_max=effective_max,
                on_max_iterations=boundary_callback,
                callback=callback,
                run_id=run_id,
                emit_event=self.emit_event,
                before_wait=before_max_wait,
            )
            effective_max = boundary.effective_max
            max_interaction_request = (
                max_adapter.interaction_request
                if max_adapter is not None
                else None
            )
            if max_interaction_request is not None:
                state.last_continuation = None
                state.suspend_state.signal_kind = None
                state.suspend_state.payload = {}
            if boundary.should_finish:
                return finish_max_iterations_run(
                    state,
                    callback=callback,
                    run_id=run_id,
                    emit_run_max_iterations=boundary.emit_run_max_iterations_on_finish,
                    emit_event=terminal_emit_event,
                    dispatch_run_finalizing=dispatch_run_finalizing,
                )
            if state.run_status == "max_iterations":
                state.run_status = "running"

            self.emit_event(
                callback,
                "iteration_started",
                run_id,
                **build_iteration_started_payload(state),
            )
            turn = self.step_once(
                state,
                payload=payload,
                toolkit=runtime_toolkit,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                emit_stream=True,
                response_format=response_format,
                on_tool_confirm=on_tool_confirm,
                on_human_input=on_human_input,
                max_iterations=effective_max,
                tool_runtime_plugins=tool_runtime_plugins,
                tool_runtime_config=tool_runtime_config,
                execution_guard=execution_guard,
            )
            if state.run_status == "awaiting_interaction":
                response_received_emitted = False
                while state.run_status == "awaiting_interaction":
                    suspended = finish_awaiting_interaction_run(
                        state,
                        callback=callback,
                        run_id=run_id,
                        dispatch_on_suspend=dispatch_on_suspend,
                    )
                    durable_request = suspended.interaction_request
                    continuation = suspended.continuation
                    if (
                        not isinstance(durable_request, dict)
                        or not isinstance(continuation, dict)
                        or self._interaction_runtime is None
                    ):
                        raise InteractionIntegrityError(
                            "awaiting_interaction requires a durable request and continuation"
                        )
                    wait_revision = current_session_revision()
                    released_for_wait = (
                        execution_guard is not None
                        and wait_revision is not None
                    )
                    if released_for_wait:
                        execution_guard.release_for_wait()
                    if not response_received_emitted:
                        self.emit_event(
                            callback,
                            "response_received",
                            run_id,
                            **build_response_received_payload(state, turn),
                        )
                        response_received_emitted = True
                    self.emit_event(
                        callback,
                        "interaction_requested",
                        run_id,
                        iteration=max(0, int(state.iteration) - 1),
                        interaction_request=copy.deepcopy(durable_request),
                    )
                    if not callable(on_tool_confirm):
                        return suspended
                    if (
                        durable_request.get("kind")
                        != INTERACTION_KIND_TOOL_APPROVAL
                    ):
                        raise InteractionNotPendingError(
                            "awaiting interaction is not a tool approval"
                        )
                    confirmation_request = (
                        self._tool_confirmation_request_from_interaction(
                            durable_request
                        )
                    )
                    response = on_tool_confirm(confirmation_request)
                    durable_snapshot = require_unapplied_callback_receipt(
                        self._interaction_runtime.record_receipt(
                            str(state.session_state.session_id or ""),
                            interaction_id=str(
                                durable_request.get("interaction_id") or ""
                            ),
                            response=response,
                            submitted_by="callback:on_tool_confirm",
                            expected_revision=wait_revision,
                        )
                    )
                    persisted_revision = durable_snapshot.session_snapshot.revision
                    state.memory_state.update(
                        {
                            "session_revision": persisted_revision,
                            "session_snapshot": copy.deepcopy(
                                durable_snapshot.session_snapshot.state
                            ),
                        }
                    )
                    state.component_bucket("memory")["state"] = copy.deepcopy(
                        state.memory_state
                    )
                    if released_for_wait:
                        execution_guard.reacquire(
                            expected_revision=persisted_revision,
                        )
                    elif execution_guard is not None:
                        execution_guard.assert_active()
                    self._dispatch_tool_approval_resume(
                        state,
                        continuation=continuation,
                        interaction_request=durable_request,
                        response=durable_snapshot.response or {},
                        payload=payload,
                        response_format=response_format,
                        callback=callback,
                        verbose=verbose,
                        on_tool_confirm=on_tool_confirm,
                        on_human_input=on_human_input,
                        max_iterations=effective_max,
                        toolkit=runtime_toolkit,
                        run_id=run_id,
                        tool_runtime_plugins=tool_runtime_plugins,
                        tool_runtime_config=tool_runtime_config,
                        execution_guard=execution_guard,
                    )
                self.emit_event(
                    callback,
                    "iteration_completed",
                    run_id,
                    **build_iteration_completed_payload(
                        state,
                        has_tool_calls=True,
                    ),
                )
                continue
            if state.run_status == "awaiting_human_input":
                suspended = finish_awaiting_human_input_run(
                    state,
                    callback=callback,
                    run_id=run_id,
                    dispatch_on_suspend=dispatch_on_suspend,
                )
                request = state.tool_batch_state.human_input_request
                continuation = suspended.continuation
                durable_request = suspended.interaction_request
                wait_revision = current_session_revision()
                durable_wait = (
                    isinstance(durable_request, dict)
                    and self._interaction_runtime is not None
                    and bool(state.session_state.session_id)
                )
                released_for_wait = False
                if durable_wait and execution_guard is not None and wait_revision is not None:
                    execution_guard.release_for_wait()
                    released_for_wait = True
                self.emit_event(
                    callback,
                    "response_received",
                    run_id,
                    **build_response_received_payload(state, turn),
                )
                if request is not None:
                    self.emit_event(
                        callback,
                        "human_input_requested",
                        run_id,
                        iteration=max(0, int(state.iteration) - 1),
                        **(
                            {
                                "interaction_id": str(
                                    durable_request.get("interaction_id") or ""
                                ),
                                "interaction_request": copy.deepcopy(
                                    durable_request
                                ),
                            }
                            if isinstance(durable_request, dict)
                            else {}
                        ),
                        **request.to_dict(),
                    )
                if not callable(on_human_input) or request is None or continuation is None:
                    return suspended
                if durable_wait:
                    response = on_human_input(request)
                    durable_snapshot = require_unapplied_callback_receipt(
                        self._interaction_runtime.record_receipt(
                            str(state.session_state.session_id or ""),
                            interaction_id=str(
                                durable_request.get("interaction_id") or ""
                            ),
                            response=response,
                            submitted_by="callback:on_human_input",
                            expected_revision=wait_revision,
                        )
                    )
                    response = durable_snapshot.response
                    persisted_revision = durable_snapshot.session_snapshot.revision
                    state.memory_state.update(
                        {
                            "session_revision": persisted_revision,
                            "session_snapshot": copy.deepcopy(
                                durable_snapshot.session_snapshot.state
                            ),
                        }
                    )
                    state.component_bucket("memory")["state"] = copy.deepcopy(
                        state.memory_state
                    )
                    if released_for_wait:
                        execution_guard.reacquire(
                            expected_revision=persisted_revision,
                        )
                    elif execution_guard is not None:
                        execution_guard.assert_active()
                else:
                    if execution_guard is not None and wait_revision is not None:
                        execution_guard.release_for_wait()
                        released_for_wait = True
                    response = on_human_input(request)
                    if released_for_wait:
                        execution_guard.reacquire(
                            expected_revision=wait_revision,
                        )
                    elif execution_guard is not None:
                        execution_guard.assert_active()
                self._dispatch_on_resume(
                    state,
                    continuation=continuation,
                    response=response,
                    callback=callback,
                    run_id=run_id,
                    execution_guard=execution_guard,
                )
                continue
            self.emit_event(
                callback,
                "response_received",
                run_id,
                **build_response_received_payload(state, turn),
            )
            if state.run_status == "completed":
                return finish_completed_run(
                    state,
                    callback=callback,
                    run_id=run_id,
                    emit_event=terminal_emit_event,
                    dispatch_run_finalizing=dispatch_run_finalizing,
                )
            if turn.tool_calls:
                self.emit_event(
                    callback,
                    "iteration_completed",
                    run_id,
                    **build_iteration_completed_payload(state, has_tool_calls=True),
                )
                continue
            return finish_completed_run(
                state,
                callback=callback,
                run_id=run_id,
                emit_event=terminal_emit_event,
                dispatch_run_finalizing=dispatch_run_finalizing,
            )

    def run(
        self,
        messages: list[dict[str, Any]],
        *,
        payload: dict[str, Any] | None = None,
        response_format: ResponseFormat | None = None,
        callback: Any = None,
        verbose: bool = False,
        max_iterations: int = 6,
        previous_response_id: str | None = None,
        on_tool_confirm: Any = None,
        on_human_input: Any = None,
        on_max_iterations: Any = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        max_context_window_tokens: int | None = None,
        toolkit: Toolkit | None = None,
        run_id: str | None = None,
        tool_runtime_plugins: list[Any] | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
        _provider_replay_frame: dict[str, Any] | None = None,
        _execution_guard: ExecutionGuard | None = None,
    ) -> KernelRunResult:
        plan = prepare_fresh_run_invocation(
            messages=messages,
            payload=payload,
            model_io=self._model_io,
            provider=provider,
            model=model,
            previous_response_id=previous_response_id,
            session_id=session_id,
            memory_namespace=memory_namespace,
            max_context_window_tokens=max_context_window_tokens,
            run_id=run_id,
            run_id_factory=lambda: str(uuid.uuid4()),
        )
        if isinstance(_provider_replay_frame, dict):
            set_provider_replay_frame(plan.state, _provider_replay_frame)
        with self._scope_for_session(
            session_id=session_id,
            execution_guard=_execution_guard,
        ) as execution_guard:
            return self._run_state(
                plan.state,
                payload=plan.payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                max_iterations=max_iterations,
                on_tool_confirm=on_tool_confirm,
                on_human_input=on_human_input,
                on_max_iterations=on_max_iterations,
                toolkit=toolkit,
                run_id=plan.run_id,
                tool_runtime_plugins=tool_runtime_plugins,
                tool_runtime_config=tool_runtime_config,
                execution_guard=execution_guard,
            )

    def resume_human_input(
        self,
        *,
        conversation: list[dict[str, Any]],
        continuation: dict[str, Any],
        response: dict[str, Any] | Any = None,
        payload: dict[str, Any] | None = None,
        response_format: ResponseFormat | None = None,
        callback: Any = None,
        verbose: bool = False,
        on_tool_confirm: Any = None,
        on_human_input: Any = None,
        on_max_iterations: Any = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        toolkit: Toolkit | None = None,
        run_id: str | None = None,
        tool_runtime_plugins: list[Any] | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
        _execution_guard: ExecutionGuard | None = None,
    ) -> KernelRunResult:
        plan = prepare_resume_run_invocation(
            conversation=conversation,
            continuation=continuation,
            payload=payload,
            response_format=response_format,
            fallback_provider=self._infer_provider(),
            fallback_model=self._infer_model(),
            session_id=session_id,
            memory_namespace=memory_namespace,
            run_id=run_id,
            run_id_factory=lambda: str(uuid.uuid4()),
        )
        with self._scope_for_session(
            session_id=session_id,
            execution_guard=_execution_guard,
        ) as execution_guard:
            resolved_response = copy.deepcopy(response)
            if self._interaction_runtime is not None and session_id:
                checkpoint = (
                    self._interaction_runtime.memory_runtime.load_execution_checkpoint(
                        session_id
                    )
                )
                interaction_ref = (
                    checkpoint.get("interaction_ref")
                    if isinstance(checkpoint, dict)
                    else None
                )
                if isinstance(interaction_ref, dict):
                    interaction_id = str(
                        interaction_ref.get("interaction_id") or ""
                    )
                    continuation_interaction_id = continuation.get("interaction_id")
                    if (
                        isinstance(continuation_interaction_id, str)
                        and continuation_interaction_id
                        and continuation_interaction_id != interaction_id
                    ):
                        raise InteractionNotPendingError(
                            "resume continuation targets a different interaction"
                        )
                    pending = self._interaction_runtime.load(
                        session_id,
                        interaction_id=interaction_id,
                        require_active=True,
                    )
                    ensure_interaction_runtime_matches(
                        pending.request,
                        provider=self._infer_provider(),
                        model=self._infer_model(),
                        checkpoint=checkpoint,
                        continuation=continuation,
                    )
                    if resolved_response is None:
                        durable_snapshot = self._interaction_runtime.require_receipt(
                            session_id,
                            interaction_id=interaction_id,
                        )
                    else:
                        if pending.request.kind != INTERACTION_KIND_HUMAN_INPUT:
                            raise InteractionNotPendingError(
                                "pending interaction is not a human-input request"
                            )
                        durable_snapshot = self._interaction_runtime.record_receipt(
                            session_id,
                            interaction_id=interaction_id,
                            response=resolved_response,
                            submitted_by="api:resume_human_input",
                            expected_revision=pending.session_snapshot.revision,
                            execution_fence=(
                                execution_guard.fence
                                if execution_guard is not None
                                else None
                            ),
                        )
                    if (
                        durable_snapshot.request.kind
                        != INTERACTION_KIND_HUMAN_INPUT
                    ):
                        raise InteractionNotPendingError(
                            "pending interaction is not a human-input request"
                        )
                    resolved_response = durable_snapshot.response
                elif resolved_response is None:
                    raise InteractionNotPendingError(
                        "legacy human-input resume requires an explicit response"
                    )
            elif resolved_response is None:
                raise InteractionNotPendingError(
                    "human-input resume requires an explicit response"
                )
            self._dispatch_bootstrap(
                plan.state,
                payload=plan.payload,
                response_format=plan.response_format,
                callback=callback,
                verbose=verbose,
                toolkit=toolkit,
                run_id=plan.run_id,
                resume_mode=True,
                continuation=continuation,
                tool_runtime_config=tool_runtime_config,
                execution_guard=execution_guard,
            )
            self._dispatch_on_resume(
                plan.state,
                continuation=continuation,
                response=resolved_response,
                callback=callback,
                run_id=plan.run_id,
                execution_guard=execution_guard,
            )
            return self._run_state(
                plan.state,
                payload=plan.payload,
                response_format=plan.response_format,
                callback=callback,
                verbose=verbose,
                max_iterations=plan.max_iterations,
                on_tool_confirm=on_tool_confirm,
                on_human_input=on_human_input,
                on_max_iterations=on_max_iterations,
                toolkit=toolkit,
                run_id=plan.run_id,
                skip_bootstrap=True,
                tool_runtime_plugins=tool_runtime_plugins,
                tool_runtime_config=tool_runtime_config,
                execution_guard=execution_guard,
            )

    def resume_interaction(
        self,
        *,
        session_id: str,
        conversation: list[dict[str, Any]] | None = None,
        continuation: dict[str, Any] | None = None,
        response: Any = None,
        submitted_by: str = "api:resume_interaction",
        payload: dict[str, Any] | None = None,
        response_format: ResponseFormat | None = None,
        callback: Any = None,
        verbose: bool = False,
        on_tool_confirm: Any = None,
        on_human_input: Any = None,
        on_max_iterations: Any = None,
        memory_namespace: str | None = None,
        toolkit: Toolkit | None = None,
        run_id: str | None = None,
        tool_runtime_plugins: list[Any] | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
        _execution_guard: ExecutionGuard | None = None,
    ) -> KernelRunResult:
        if self._interaction_runtime is None:
            raise InteractionNotPendingError(
                "resume_interaction requires a memory-backed interaction runtime"
            )
        if not isinstance(session_id, str) or not session_id:
            raise InteractionNotPendingError(
                "resume_interaction requires a non-empty session_id"
            )

        with self._scope_for_session(
            session_id=session_id,
            execution_guard=_execution_guard,
        ) as execution_guard:
            checkpoint = (
                self._interaction_runtime.memory_runtime.load_execution_checkpoint(
                    session_id
                )
            )
            if not isinstance(checkpoint, dict):
                raise InteractionNotPendingError(
                    "session has no durable execution checkpoint"
                )
            interaction_ref = checkpoint.get("interaction_ref")
            if not isinstance(interaction_ref, dict):
                raise InteractionNotPendingError(
                    "execution checkpoint has no durable interaction"
                )
            interaction_id = str(interaction_ref.get("interaction_id") or "")
            pending_interaction = self._interaction_runtime.load(
                session_id,
                interaction_id=interaction_id,
                require_active=True,
            )
            ensure_interaction_runtime_matches(
                pending_interaction.request,
                provider=self._infer_provider(),
                model=self._infer_model(),
                checkpoint=checkpoint,
                continuation=(
                    continuation
                    if isinstance(continuation, dict)
                    else checkpoint.get("continuation")
                ),
            )
            if response is None:
                durable_snapshot = self._interaction_runtime.require_receipt(
                    session_id,
                    interaction_id=interaction_id,
                )
            else:
                pending = self._interaction_runtime.load(
                    session_id,
                    interaction_id=interaction_id,
                    require_active=True,
                )
                durable_snapshot = self._interaction_runtime.record_receipt(
                    session_id,
                    interaction_id=interaction_id,
                    response=response,
                    submitted_by=submitted_by,
                    expected_revision=pending.session_snapshot.revision,
                    execution_fence=(
                        execution_guard.fence
                        if execution_guard is not None
                        else None
                    ),
                )
            if (
                durable_snapshot.checkpoint_id != checkpoint.get("checkpoint_id")
                or durable_snapshot.request.request_digest
                != interaction_ref.get("request_digest")
            ):
                raise InteractionIntegrityError(
                    "durable interaction is not bound to the active checkpoint"
                )

            resolved_conversation = (
                copy.deepcopy(conversation)
                if isinstance(conversation, list)
                else copy.deepcopy(checkpoint.get("transcript") or [])
            )
            resolved_continuation = (
                copy.deepcopy(continuation)
                if isinstance(continuation, dict)
                else copy.deepcopy(checkpoint.get("continuation") or {})
            )
            if (
                resolved_continuation.get("interaction_id")
                != durable_snapshot.request.interaction_id
            ):
                raise InteractionIntegrityError(
                    "resume continuation does not match the durable interaction"
                )
            plan = prepare_resume_run_invocation(
                conversation=resolved_conversation,
                continuation=resolved_continuation,
                payload=payload,
                response_format=response_format,
                fallback_provider=self._infer_provider(),
                fallback_model=self._infer_model(),
                session_id=session_id,
                memory_namespace=memory_namespace,
                run_id=run_id,
                run_id_factory=lambda: str(uuid.uuid4()),
            )
            self._dispatch_bootstrap(
                plan.state,
                payload=plan.payload,
                response_format=plan.response_format,
                callback=callback,
                verbose=verbose,
                toolkit=toolkit,
                run_id=plan.run_id,
                resume_mode=True,
                continuation=resolved_continuation,
                tool_runtime_config=tool_runtime_config,
                execution_guard=execution_guard,
            )

            if durable_snapshot.request.kind == INTERACTION_KIND_HUMAN_INPUT:
                if (
                    resolved_continuation.get("type")
                    != HUMAN_INPUT_CONTINUATION_TYPE
                ):
                    raise InteractionIntegrityError(
                        "human-input receipt requires a matching continuation"
                    )
                self._dispatch_on_resume(
                    plan.state,
                    continuation=resolved_continuation,
                    response=durable_snapshot.response,
                    callback=callback,
                    run_id=plan.run_id,
                    execution_guard=execution_guard,
                )
                return self._run_state(
                    plan.state,
                    payload=plan.payload,
                    response_format=plan.response_format,
                    callback=callback,
                    verbose=verbose,
                    max_iterations=plan.max_iterations,
                    on_tool_confirm=on_tool_confirm,
                    on_human_input=on_human_input,
                    on_max_iterations=on_max_iterations,
                    toolkit=toolkit,
                    run_id=plan.run_id,
                    skip_bootstrap=True,
                    tool_runtime_plugins=tool_runtime_plugins,
                    tool_runtime_config=tool_runtime_config,
                    execution_guard=execution_guard,
                )
            if durable_snapshot.request.kind == INTERACTION_KIND_TOOL_APPROVAL:
                self._dispatch_tool_approval_resume(
                    plan.state,
                    continuation=resolved_continuation,
                    interaction_request=durable_snapshot.request.to_dict(),
                    response=durable_snapshot.response or {},
                    payload=plan.payload,
                    response_format=plan.response_format,
                    callback=callback,
                    verbose=verbose,
                    on_tool_confirm=on_tool_confirm,
                    on_human_input=on_human_input,
                    max_iterations=plan.max_iterations,
                    toolkit=toolkit if toolkit is not None else Toolkit(),
                    run_id=plan.run_id,
                    tool_runtime_plugins=tool_runtime_plugins,
                    tool_runtime_config=tool_runtime_config,
                    execution_guard=execution_guard,
                )
                if plan.state.run_status == "awaiting_interaction":
                    return finish_awaiting_interaction_run(
                        plan.state,
                        callback=callback,
                        run_id=plan.run_id,
                        dispatch_on_suspend=partial(
                            self._dispatch_on_suspend,
                            execution_guard=execution_guard,
                        ),
                    )
                self.emit_event(
                    callback,
                    "iteration_completed",
                    plan.run_id,
                    **build_iteration_completed_payload(
                        plan.state,
                        has_tool_calls=True,
                    ),
                )
                return self._run_state(
                    plan.state,
                    payload=plan.payload,
                    response_format=plan.response_format,
                    callback=callback,
                    verbose=verbose,
                    max_iterations=plan.max_iterations,
                    on_tool_confirm=on_tool_confirm,
                    on_human_input=on_human_input,
                    on_max_iterations=on_max_iterations,
                    toolkit=toolkit,
                    run_id=plan.run_id,
                    skip_bootstrap=True,
                    tool_runtime_plugins=tool_runtime_plugins,
                    tool_runtime_config=tool_runtime_config,
                    execution_guard=execution_guard,
                )
            if durable_snapshot.request.kind != INTERACTION_KIND_MAX_BUDGET:
                raise InteractionNotPendingError(
                    "unsupported durable interaction kind"
                )
            if resolved_continuation.get("type") != "max_budget_continuation":
                raise InteractionIntegrityError(
                    "max-budget receipt requires a max_budget_continuation"
                )
            decision = durable_snapshot.response or {}
            approved = bool(decision.get("approved"))
            extra_iterations = (
                int(decision.get("extra_iterations") or 0)
                if approved
                else 0
            )
            plan.state.last_continuation = None
            plan.state.suspend_state.signal_kind = None
            plan.state.suspend_state.payload = {}
            return self._run_state(
                plan.state,
                payload=plan.payload,
                response_format=plan.response_format,
                callback=callback,
                verbose=verbose,
                max_iterations=extra_iterations,
                on_tool_confirm=on_tool_confirm,
                on_human_input=on_human_input,
                on_max_iterations=(on_max_iterations if approved else None),
                toolkit=toolkit,
                run_id=plan.run_id,
                skip_bootstrap=True,
                tool_runtime_plugins=tool_runtime_plugins,
                tool_runtime_config=tool_runtime_config,
                execution_guard=execution_guard,
            )
