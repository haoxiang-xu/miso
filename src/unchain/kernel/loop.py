from __future__ import annotations

import copy
import uuid
from typing import Any

from ..providers.model_turn_runtime import apply_model_turn_result, fetch_model_turn
from ..retry import RetryConfig
from ..schemas import ResponseFormat
from ..tools.toolkit import Toolkit
from .delta import HarnessDelta
from .harness import HarnessContext, RuntimeHarness, RuntimePhase
from .lifecycle_events import (
    build_iteration_completed_payload,
    build_iteration_started_payload,
    build_response_received_payload,
    build_run_started_payload,
)
from .model_io import ModelIO
from .run_limits import resolve_max_iterations_boundary
from .run_outcomes import (
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
from .types import KernelRunResult, ModelTurnResult


_DURABLE_BARRIER_PHASES = frozenset({"suspend_persist", "finalize_persist"})


class KernelLoop:
    """Minimal harness-driven loop skeleton for the new kernel."""

    def __init__(
        self,
        *,
        harnesses: list[RuntimeHarness] | None = None,
        model_io: ModelIO | None = None,
        retry_config: RetryConfig | None = None,
    ) -> None:
        self._harnesses: list[RuntimeHarness] = []
        self._model_io = model_io
        self._retry_config: RetryConfig = retry_config if retry_config is not None else RetryConfig()
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

    @model_io.setter
    def model_io(self, value: ModelIO | None) -> None:
        self._model_io = value

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
    ) -> ModelTurnResult:
        runtime_toolkit = toolkit if toolkit is not None else Toolkit()
        current_iteration = int(state.iteration)
        state.rebuild_working_version_from_transcript(
            metadata={
                "iteration": current_iteration,
                "transcript_message_count": len(state.transcript),
            }
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
        }

        self.dispatch_phase(state, phase="before_model", event=phase_event)
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
        )
        self.apply_model_turn(state, turn)

        after_model_event = {
            **phase_event,
            "turn_result": turn,
        }
        self.dispatch_phase(state, phase="after_model", event=after_model_event)

        tool_calls = list(state.pending_tool_calls)
        if tool_calls:
            for index, tool_call in enumerate(tool_calls):
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
            self.dispatch_phase(
                state,
                phase="after_tool_batch",
                event={
                    **after_model_event,
                    "tool_calls": tool_calls,
                },
            )
        else:
            self.dispatch_phase(state, phase="before_commit", event=after_model_event)

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
    ) -> None:
        runtime_toolkit = toolkit if toolkit is not None else Toolkit()
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
            },
        )

    def _dispatch_run_finalizing(
        self,
        state: RunState,
        *,
        callback: Any,
        run_id: str,
        iteration: int,
        status: str,
    ) -> None:
        event = {
            "callback": callback,
            "run_id": run_id,
            "iteration": int(iteration),
            "status": status,
            "loop": self,
        }
        self.dispatch_phase(state, phase="run_finalizing", event=event)
        self.dispatch_phase(state, phase="finalize_persist", event=event)

    def _dispatch_on_suspend(
        self,
        state: RunState,
        *,
        callback: Any,
        run_id: str,
        iteration: int,
        status: str,
    ) -> None:
        event = {
            "callback": callback,
            "run_id": run_id,
            "iteration": int(iteration),
            "status": status,
            "loop": self,
        }
        self.dispatch_phase(state, phase="on_suspend", event=event)
        self.dispatch_phase(state, phase="suspend_persist", event=event)

    def _dispatch_on_resume(
        self,
        state: RunState,
        *,
        continuation: dict[str, Any],
        response: Any,
        callback: Any,
        run_id: str,
    ) -> None:
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
            },
        )

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
    ) -> KernelRunResult:
        if self._model_io is None:
            raise RuntimeError("KernelLoop.model_io is not configured")
        prepare_state_for_execution(state, model_io=self._model_io)
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
        while True:
            def persist_before_max_iterations_wait() -> None:
                state.run_status = "max_iterations"
                self._dispatch_on_suspend(
                    state,
                    callback=callback,
                    run_id=run_id,
                    iteration=int(state.iteration),
                    status="max_iterations",
                )

            boundary = resolve_max_iterations_boundary(
                state,
                effective_max=effective_max,
                on_max_iterations=on_max_iterations,
                callback=callback,
                run_id=run_id,
                emit_event=self.emit_event,
                before_wait=persist_before_max_iterations_wait,
            )
            effective_max = boundary.effective_max
            if boundary.should_finish:
                return finish_max_iterations_run(
                    state,
                    callback=callback,
                    run_id=run_id,
                    emit_run_max_iterations=boundary.emit_run_max_iterations_on_finish,
                    emit_event=self.emit_event,
                    dispatch_run_finalizing=self._dispatch_run_finalizing,
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
            )
            if state.run_status == "awaiting_human_input":
                suspended = finish_awaiting_human_input_run(
                    state,
                    callback=callback,
                    run_id=run_id,
                    dispatch_on_suspend=self._dispatch_on_suspend,
                )
                request = state.tool_batch_state.human_input_request
                continuation = suspended.continuation
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
                        **request.to_dict(),
                    )
                if not callable(on_human_input) or request is None or continuation is None:
                    return suspended
                response = on_human_input(request)
                self._dispatch_on_resume(
                    state,
                    continuation=continuation,
                    response=response,
                    callback=callback,
                    run_id=run_id,
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
                    emit_event=self.emit_event,
                    dispatch_run_finalizing=self._dispatch_run_finalizing,
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
                emit_event=self.emit_event,
                dispatch_run_finalizing=self._dispatch_run_finalizing,
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
        )

    def resume_human_input(
        self,
        *,
        conversation: list[dict[str, Any]],
        continuation: dict[str, Any],
        response: dict[str, Any],
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
        )
        self._dispatch_on_resume(
            plan.state,
            continuation=continuation,
            response=response,
            callback=callback,
            run_id=plan.run_id,
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
        )
