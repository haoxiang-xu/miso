from __future__ import annotations

from typing import Any

from .delta import HarnessDelta
from .harness import HarnessContext, RuntimeHarness, RuntimePhase
from .model_io import ModelIO, ModelTurnRequest
from .state import RunState
from .types import ModelTurnResult
from ..tools.toolkit import Toolkit


class KernelLoop:
    """Minimal harness-driven loop skeleton for the new kernel."""

    def __init__(
        self,
        *,
        harnesses: list[RuntimeHarness] | None = None,
        model_io: ModelIO | None = None,
    ) -> None:
        self._harnesses: list[RuntimeHarness] = []
        self._model_io = model_io
        for harness in harnesses or []:
            self.register_harness(harness)

    @property
    def harnesses(self) -> list[RuntimeHarness]:
        return list(self._harnesses)

    def register_harness(self, harness: RuntimeHarness) -> None:
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
            delta = harness.build_delta(context)
            if delta is None:
                continue
            if not isinstance(delta, HarnessDelta):
                raise TypeError(
                    f"harness '{harness.name}' returned {type(delta).__name__}, expected HarnessDelta"
                )
            state.apply_delta(delta)
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
        if self._model_io is None:
            raise RuntimeError("KernelLoop.model_io is not configured")
        request = ModelTurnRequest(
            messages=state.latest_messages(),
            payload=dict(payload or {}),
            response_format=response_format,
            callback=callback,
            verbose=verbose,
            run_id=run_id,
            iteration=state.iteration,
            toolkit=toolkit if toolkit is not None else Toolkit(),
            emit_stream=emit_stream,
            previous_response_id=state.provider_state.previous_response_id,
            openai_text_format=openai_text_format,
        )
        return self._model_io.fetch_turn(request)

    def apply_model_turn(
        self,
        state: RunState,
        turn: ModelTurnResult,
        *,
        created_by: str = "kernel.model_turn",
    ) -> RunState:
        state.apply_delta(
            HarnessDelta.append(
                created_by=created_by,
                messages=turn.assistant_messages,
                state_updates={
                    "transcript_append": turn.assistant_messages,
                    "pending_tool_calls": list(turn.tool_calls),
                    "last_model_turn": turn,
                    "provider_state": {
                        "previous_response_id": turn.response_id,
                    },
                    "token_state": {
                        "consumed_tokens": state.token_state.consumed_tokens + int(turn.consumed_tokens or 0),
                        "input_tokens": state.token_state.input_tokens + int(turn.input_tokens or 0),
                        "output_tokens": state.token_state.output_tokens + int(turn.output_tokens or 0),
                        "last_turn_tokens": int(turn.consumed_tokens or 0),
                        "last_turn_input_tokens": int(turn.input_tokens or 0),
                        "last_turn_output_tokens": int(turn.output_tokens or 0),
                    },
                },
                trace={
                    "response_id": turn.response_id,
                    "assistant_message_count": len(turn.assistant_messages),
                    "tool_call_count": len(turn.tool_calls),
                },
            )
        )
        return state

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
