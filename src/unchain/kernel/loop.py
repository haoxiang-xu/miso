from __future__ import annotations

import copy
import uuid
from typing import Any

from ..memory import KernelMemoryRuntime
from ..schemas import ResponseFormat
from ..tools import (
    HumanInputResumeHarness,
    OBSERVATION_MAX_OUTPUT_TOKENS,
    OBSERVATION_RECENT_MESSAGES,
    OBSERVATION_SYSTEM_PROMPT,
    ToolExecutionHarness,
    ToolPromptHarness,
)
from ..tools.toolkit import Toolkit
from .delta import HarnessDelta
from .harness import HarnessContext, RuntimeHarness, RuntimePhase
from .model_io import ModelIO, ModelTurnRequest
from .state import RunState
from .types import KernelRunResult, ModelTurnResult, TokenUsage


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
        self._memory_runtime: KernelMemoryRuntime | None = None
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

    def register_memory_harness(self, memory_harness: RuntimeHarness) -> None:
        self.register_harness(memory_harness)

    def attach_memory(self, memory_runtime: KernelMemoryRuntime) -> None:
        self._memory_runtime = memory_runtime
        self._ensure_memory_components()

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
        request_messages = (
            copy.deepcopy(state.next_model_input)
            if isinstance(state.next_model_input, list)
            else state.latest_messages()
        )
        request = ModelTurnRequest(
            messages=request_messages,
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
                    "next_model_input": None,
                    "run_status": "running",
                    "token_state": {
                        "consumed_tokens": state.token_state.consumed_tokens + int(turn.consumed_tokens or 0),
                        "input_tokens": state.token_state.input_tokens + int(turn.input_tokens or 0),
                        "output_tokens": state.token_state.output_tokens + int(turn.output_tokens or 0),
                        "cache_read_input_tokens": state.token_state.cache_read_input_tokens + int(turn.cache_read_input_tokens or 0),
                        "cache_creation_input_tokens": state.token_state.cache_creation_input_tokens + int(turn.cache_creation_input_tokens or 0),
                        "last_turn_tokens": int(turn.consumed_tokens or 0),
                        "last_turn_input_tokens": int(turn.input_tokens or 0),
                        "last_turn_output_tokens": int(turn.output_tokens or 0),
                        "last_turn_cache_read_input_tokens": int(turn.cache_read_input_tokens or 0),
                        "last_turn_cache_creation_input_tokens": int(turn.cache_creation_input_tokens or 0),
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
        max_iterations: int = 0,
        on_input: Any = None,
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
            "max_iterations": max_iterations,
            "on_input": on_input,
            "supports_tools": True,
            "loop": self,
            "tool_runtime_plugins": list(tool_runtime_plugins or []),
            "tool_runtime_config": copy.deepcopy(tool_runtime_config or {}),
        }

        if self._memory_runtime is not None and current_iteration > 0:
            state.memory_prepare_info = {}
            state.component_bucket("memory")["prepare_info"] = {}
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
            if self._memory_runtime is not None:
                state.memory_commit_info = {}
                state.component_bucket("memory")["commit_info"] = {}
            self.dispatch_phase(state, phase="before_commit", event=after_model_event)
            pass  # memory_commit event removed — harness logic retained above

        state.iteration = current_iteration + 1
        return turn

    def _iter_phase_harnesses(self, phase: RuntimePhase) -> list[RuntimeHarness]:
        return [harness for harness in self._harnesses if phase in harness.phases]

    def _ensure_runtime_harnesses(self) -> None:
        existing_names = {harness.name for harness in self._harnesses}
        if "tool_prompt" not in existing_names:
            self.register_harness(ToolPromptHarness())
        if "tool_execution" not in existing_names:
            self.register_harness(ToolExecutionHarness())
        if "human_input_resume" not in existing_names:
            self.register_harness(HumanInputResumeHarness())

    def _ensure_memory_components(self) -> None:
        if self._memory_runtime is None:
            return
        existing_names = {harness.name for harness in self._harnesses}
        for component in self._memory_runtime.build_default_components():
            if component.name in existing_names:
                continue
            self.register_harness(component)
            existing_names.add(component.name)

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
        tool_runtime_config: dict[str, Any] | None = None,
    ) -> None:
        if self._memory_runtime is None:
            return
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
                "loop": self,
                "tool_runtime_config": copy.deepcopy(tool_runtime_config or {}),
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
        if self._model_io is None:
            return None
        provider = getattr(self._model_io, "provider", None)
        if isinstance(provider, str) and provider.strip():
            return provider.strip()
        if hasattr(self._model_io, "engine"):
            engine = getattr(self._model_io, "engine", None)
            engine_provider = getattr(engine, "provider", None)
            if isinstance(engine_provider, str) and engine_provider.strip():
                return engine_provider.strip()
        if self._model_io.__class__.__name__ == "OpenAIModelIO":
            return "openai"
        return None

    def _infer_model(self) -> str | None:
        if self._model_io is None:
            return None
        model = getattr(self._model_io, "model", None)
        if isinstance(model, str) and model.strip():
            return model.strip()
        if hasattr(self._model_io, "engine"):
            engine = getattr(self._model_io, "engine", None)
            engine_model = getattr(engine, "model", None)
            if isinstance(engine_model, str) and engine_model.strip():
                return engine_model.strip()
        return None

    def _serialize_response_format(
        self,
        response_format: ResponseFormat | None,
    ) -> dict[str, Any] | None:
        if response_format is None:
            return None
        return {
            "name": response_format.name,
            "schema": copy.deepcopy(response_format.schema),
            "required": list(response_format.required),
        }

    def _deserialize_response_format(
        self,
        raw: dict[str, Any] | None,
    ) -> ResponseFormat | None:
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        schema = raw.get("schema")
        required = raw.get("required")
        if not isinstance(name, str) or not isinstance(schema, dict):
            return None
        required_list = required if isinstance(required, list) else None
        return ResponseFormat(name=name, schema=schema, required=required_list)

    @staticmethod
    def _ensure_json_safe(obj: Any) -> Any:
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, dict):
            return {
                k: KernelLoop._ensure_json_safe(v)
                for k, v in obj.items()
                if isinstance(k, str) and isinstance(v, (str, int, float, bool, type(None), dict, list, tuple))
            }
        if isinstance(obj, (list, tuple)):
            return [
                KernelLoop._ensure_json_safe(item)
                for item in obj
                if isinstance(item, (str, int, float, bool, type(None), dict, list, tuple))
            ]
        return str(obj)

    def build_human_input_continuation(
        self,
        *,
        request: Any,
        payload: dict[str, Any],
        response_format: ResponseFormat | None,
        next_iteration: int,
        max_iterations: int,
        state: RunState,
    ) -> dict[str, Any]:
        return {
            "type": "human_input_continuation",
            "kind": getattr(request, "kind", None),
            "provider": state.provider_state.provider,
            "model": state.provider_state.model,
            "request_id": getattr(request, "request_id", None),
            "call_id": getattr(request, "request_id", None),
            "request": request.to_dict(),
            "payload": self._ensure_json_safe(copy.deepcopy(payload)),
            "response_format": self._ensure_json_safe(self._serialize_response_format(response_format)),
            "iteration": int(next_iteration),
            "max_iterations": int(max_iterations),
            "previous_response_id": state.provider_state.previous_response_id,
            "use_openai_previous_response_chain": bool(state.provider_state.use_previous_response_chain),
            "session_id": state.session_state.session_id,
            "memory_namespace": state.session_state.memory_namespace,
            "max_context_window_tokens": int(state.provider_state.max_context_window_tokens or 0),
            "consumed_tokens": int(state.token_state.consumed_tokens or 0),
            "input_tokens": int(state.token_state.input_tokens or 0),
            "output_tokens": int(state.token_state.output_tokens or 0),
            "last_turn_tokens": int(state.token_state.last_turn_tokens or 0),
            "last_turn_input_tokens": int(state.token_state.last_turn_input_tokens or 0),
            "last_turn_output_tokens": int(state.token_state.last_turn_output_tokens or 0),
        }

    def _last_assistant_text(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages or []):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
        return ""

    def observe_tool_batch(
        self,
        *,
        full_messages: list[dict[str, Any]],
        tool_messages: list[dict[str, Any]],
        payload: dict[str, Any],
        callback: Any = None,
        iteration: int = 0,
        provider: str | None = None,
    ) -> tuple[str, TokenUsage]:
        if self._model_io is None:
            return "", TokenUsage()
        observe_messages = [
            {"role": "system", "content": OBSERVATION_SYSTEM_PROMPT},
            *copy.deepcopy(list(full_messages or [])[-OBSERVATION_RECENT_MESSAGES:]),
            *copy.deepcopy(tool_messages),
            {
                "role": "user",
                "content": "Review the LAST tool result above and provide one brief actionable observation.",
            },
        ]
        observe_payload = self._build_observation_payload(
            payload or {},
            provider=provider or self._infer_provider(),
        )
        try:
            turn = self._model_io.fetch_turn(
                ModelTurnRequest(
                    messages=observe_messages,
                    payload=observe_payload,
                    response_format=None,
                    callback=None,
                    verbose=False,
                    run_id="observe",
                    iteration=iteration,
                    toolkit=Toolkit(),
                    emit_stream=False,
                    previous_response_id=None,
                )
            )
        except Exception:
            return "", TokenUsage()
        observation = (turn.final_text or self._last_assistant_text(turn.assistant_messages)).strip()
        return observation, TokenUsage(
            consumed_tokens=int(turn.consumed_tokens or 0),
            input_tokens=int(turn.input_tokens or 0),
            output_tokens=int(turn.output_tokens or 0),
        )

    def _build_observation_payload(
        self,
        payload: dict[str, Any],
        *,
        provider: str | None,
    ) -> dict[str, Any]:
        observe_payload = dict(payload or {})
        observe_payload["temperature"] = 0.2
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider == "anthropic":
            observe_payload["max_tokens"] = OBSERVATION_MAX_OUTPUT_TOKENS
            observe_payload.pop("max_output_tokens", None)
            observe_payload.pop("num_predict", None)
            return observe_payload
        if normalized_provider == "ollama":
            observe_payload["num_predict"] = OBSERVATION_MAX_OUTPUT_TOKENS
            observe_payload.pop("max_output_tokens", None)
            observe_payload.pop("max_tokens", None)
            return observe_payload
        observe_payload["max_output_tokens"] = OBSERVATION_MAX_OUTPUT_TOKENS
        observe_payload.pop("max_tokens", None)
        observe_payload.pop("num_predict", None)
        return observe_payload

    def _build_result(self, state: RunState, *, status: str) -> KernelRunResult:
        request = state.tool_batch_state.human_input_request
        return KernelRunResult(
            messages=copy.deepcopy(state.transcript),
            status=status,
            continuation=copy.deepcopy(state.last_continuation) if isinstance(state.last_continuation, dict) else None,
            human_input_request=request.to_dict() if request is not None else None,
            consumed_tokens=int(state.token_state.consumed_tokens or 0),
            input_tokens=int(state.token_state.input_tokens or 0),
            output_tokens=int(state.token_state.output_tokens or 0),
            last_turn_tokens=int(state.token_state.last_turn_tokens or 0),
            last_turn_input_tokens=int(state.token_state.last_turn_input_tokens or 0),
            last_turn_output_tokens=int(state.token_state.last_turn_output_tokens or 0),
            cache_read_input_tokens=int(state.token_state.cache_read_input_tokens or 0),
            cache_creation_input_tokens=int(state.token_state.cache_creation_input_tokens or 0),
            previous_response_id=state.provider_state.previous_response_id,
            iteration=int(state.iteration),
        )

    def _build_legacy_bundle(self, state: RunState, *, status: str) -> dict[str, Any]:
        max_ctx = max(0, int(state.provider_state.max_context_window_tokens or 0))
        last_turn_tokens = int(state.token_state.last_turn_tokens or 0)
        pct = (last_turn_tokens / max_ctx * 100.0) if max_ctx > 0 else 0.0
        request = state.tool_batch_state.human_input_request
        return {
            "model": state.provider_state.model,
            "consumed_tokens": int(state.token_state.consumed_tokens or 0),
            "input_tokens": int(state.token_state.input_tokens or 0),
            "output_tokens": int(state.token_state.output_tokens or 0),
            "last_turn_tokens": last_turn_tokens,
            "last_turn_input_tokens": int(state.token_state.last_turn_input_tokens or 0),
            "last_turn_output_tokens": int(state.token_state.last_turn_output_tokens or 0),
            "max_context_window_tokens": max_ctx,
            "context_window_used_pct": round(pct, 2),
            "status": status,
            "human_input_request": request.to_dict() if request is not None else None,
            "continuation": copy.deepcopy(state.last_continuation) if isinstance(state.last_continuation, dict) else None,
            "previous_response_id": state.provider_state.previous_response_id,
            "iteration": int(state.iteration),
        }

    def _run_state(
        self,
        state: RunState,
        *,
        payload: dict[str, Any] | None = None,
        response_format: ResponseFormat | None = None,
        callback: Any = None,
        verbose: bool = False,
        max_iterations: int = 6,
        on_input: Any = None,
        toolkit: Toolkit | None = None,
        run_id: str | None = None,
        skip_bootstrap: bool = False,
        tool_runtime_plugins: list[Any] | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
    ) -> KernelRunResult:
        if self._model_io is None:
            raise RuntimeError("KernelLoop.model_io is not configured")
        provider = str(state.provider_state.provider or self._infer_provider() or "")
        if provider not in {"openai", "anthropic", "ollama"}:
            raise NotImplementedError(
                "KernelLoop.run currently supports only provider in "
                "{'openai', 'anthropic', 'ollama'}, "
                f"got {provider!r}"
            )
        state.provider_state.provider = provider
        if not state.provider_state.model:
            state.provider_state.model = self._infer_model()
        self._ensure_runtime_harnesses()
        self._ensure_memory_components()
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
            iteration=int(state.iteration),
            provider=state.provider_state.provider,
            model=state.provider_state.model,
        )
        effective_max = int(max_iterations)
        while True:
            if int(state.iteration) >= effective_max:
                should_continue = False
                if on_input:
                    from ..types.input import InputRequest
                    resp = on_input(InputRequest(
                        kind="continue",
                        run_id=run_id,
                        call_id=None,
                        tool_name=None,
                        config={"reason": "max_iterations_reached", "iterations_used": int(state.iteration)},
                    ))
                    should_continue = resp.decision == "continued"

                if should_continue:
                    effective_max += max(1, effective_max)
                else:
                    state.run_status = "max_iterations"
                    return self._build_result(state, status="max_iterations")

            turn = self.step_once(
                state,
                payload=payload,
                toolkit=runtime_toolkit,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                emit_stream=True,
                response_format=response_format,
                max_iterations=effective_max,
                on_input=on_input,
                tool_runtime_plugins=tool_runtime_plugins,
                tool_runtime_config=tool_runtime_config,
            )
            if state.run_status == "awaiting_human_input":
                return self._build_result(state, status="awaiting_human_input")
            if state.run_status == "completed":
                self.emit_event(
                    callback,
                    "run_completed",
                    run_id,
                    iteration=max(0, int(state.iteration) - 1),
                    status="completed",
                    bundle=self._build_legacy_bundle(state, status="completed"),
                )
                return self._build_result(state, status="completed")
            if turn.tool_calls:
                continue
            self.emit_event(
                callback,
                "run_completed",
                run_id,
                iteration=max(0, int(state.iteration) - 1),
                status="completed",
                bundle=self._build_legacy_bundle(state, status="completed"),
            )
            return self._build_result(state, status="completed")

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
        on_input: Any = None,
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
        resolved_payload = dict(payload or {})
        resolved_provider = provider or self._infer_provider() or "openai"
        resolved_model = model or self._infer_model()
        resolved_run_id = str(run_id or uuid.uuid4())
        state = self.seed_state(
            messages,
            provider=resolved_provider,
            model=resolved_model,
            session_id=session_id,
            memory_namespace=memory_namespace,
            max_context_window_tokens=max_context_window_tokens,
        )
        effective_store = resolved_payload.get("store")
        if effective_store is None and self._model_io is not None:
            try:
                merged = self._model_io._merged_payload(resolved_payload)
                effective_store = merged.get("store")
            except Exception:
                pass
        use_previous_response_chain = resolved_provider == "openai" and effective_store is not False
        state.provider_state.previous_response_id = previous_response_id
        state.provider_state.use_previous_response_chain = use_previous_response_chain
        state.run_status = "running"
        return self._run_state(
            state,
            payload=resolved_payload,
            response_format=response_format,
            callback=callback,
            verbose=verbose,
            max_iterations=max_iterations,
            on_input=on_input,
            toolkit=toolkit,
            run_id=resolved_run_id,
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
        on_input: Any = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        toolkit: Toolkit | None = None,
        run_id: str | None = None,
        tool_runtime_plugins: list[Any] | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
    ) -> KernelRunResult:
        if not isinstance(conversation, list):
            raise TypeError("conversation must be a list of provider-projected messages")
        if not isinstance(continuation, dict):
            raise TypeError("continuation must be a dict returned by KernelRunResult.continuation")
        resolved_provider = str(continuation.get("provider") or self._infer_provider() or "")
        if resolved_provider not in {"openai", "anthropic", "ollama"}:
            raise NotImplementedError(
                "KernelLoop.resume_human_input currently supports only provider in "
                "{'openai', 'anthropic', 'ollama'}, "
                f"got {resolved_provider!r}"
            )
        expected_session_id = continuation.get("session_id")
        if isinstance(expected_session_id, str) and session_id is not None and session_id != expected_session_id:
            raise ValueError("resume_human_input requires the same session_id as the suspended run")
        resolved_session_id = session_id if session_id is not None else expected_session_id
        resolved_memory_namespace = (
            memory_namespace if memory_namespace is not None else continuation.get("memory_namespace")
        )
        resolved_payload = dict(payload) if payload is not None else copy.deepcopy(continuation.get("payload") or {})
        resolved_response_format = (
            response_format
            if response_format is not None
            else self._deserialize_response_format(continuation.get("response_format"))
        )
        resolved_run_id = str(run_id or uuid.uuid4())
        state = self.seed_state(
            conversation,
            provider=resolved_provider,
            model=continuation.get("model") or self._infer_model(),
            session_id=resolved_session_id if isinstance(resolved_session_id, str) else None,
            memory_namespace=resolved_memory_namespace if isinstance(resolved_memory_namespace, str) else None,
            max_context_window_tokens=int(continuation.get("max_context_window_tokens") or 0),
        )
        state.iteration = int(continuation.get("iteration") or 0)
        state.provider_state.previous_response_id = continuation.get("previous_response_id")
        state.provider_state.use_previous_response_chain = bool(
            continuation.get("use_openai_previous_response_chain", False)
        )
        state.token_state.consumed_tokens = int(continuation.get("consumed_tokens") or 0)
        state.token_state.input_tokens = int(continuation.get("input_tokens") or 0)
        state.token_state.output_tokens = int(continuation.get("output_tokens") or 0)
        state.token_state.last_turn_tokens = int(continuation.get("last_turn_tokens") or 0)
        state.token_state.last_turn_input_tokens = int(continuation.get("last_turn_input_tokens") or 0)
        state.token_state.last_turn_output_tokens = int(continuation.get("last_turn_output_tokens") or 0)
        state.run_status = "running"
        self._ensure_runtime_harnesses()
        self._ensure_memory_components()
        self._dispatch_bootstrap(
            state,
            payload=resolved_payload,
            response_format=resolved_response_format,
            callback=callback,
            verbose=verbose,
            toolkit=toolkit,
            run_id=resolved_run_id,
            resume_mode=True,
            tool_runtime_config=tool_runtime_config,
        )
        self.dispatch_phase(
            state,
            phase="on_resume",
            event={
                "continuation": copy.deepcopy(continuation),
                "response": copy.deepcopy(response),
                "callback": callback,
                "run_id": resolved_run_id,
                "loop": self,
            },
        )
        return self._run_state(
            state,
            payload=resolved_payload,
            response_format=resolved_response_format,
            callback=callback,
            verbose=verbose,
            max_iterations=int(continuation.get("max_iterations") or 6),
            on_input=on_input,
            toolkit=toolkit,
            run_id=resolved_run_id,
            skip_bootstrap=True,
            tool_runtime_plugins=tool_runtime_plugins,
            tool_runtime_config=tool_runtime_config,
        )
