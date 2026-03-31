from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from ..memory import KernelMemoryRuntime
from ..kernel.loop import KernelLoop
from ..kernel.model_io import ModelIO
from ..kernel.types import KernelRunResult
from ..schemas import ResponseFormat
from ..tools import Tool, Toolkit
from .model_io import ModelIOFactoryRegistry
from .spec import AgentSpec, AgentState


RunHook = Callable[[KernelRunResult], KernelRunResult | None]


@dataclass
class AgentCallContext:
    mode: str
    input_messages: list[dict[str, Any]] | None = None
    conversation: list[dict[str, Any]] | None = None
    continuation: dict[str, Any] | None = None
    response: dict[str, Any] | Any = None
    payload: dict[str, Any] | None = None
    response_format: ResponseFormat | None = None
    callback: Callable[[dict[str, Any]], None] | None = None
    verbose: bool = False
    max_iterations: int | None = None
    max_context_window_tokens: int | None = None
    previous_response_id: str | None = None
    on_tool_confirm: Callable[..., Any] | None = None
    on_human_input: Callable[..., Any] | None = None
    on_max_iterations: Callable[..., Any] | None = None
    session_id: str | None = None
    memory_namespace: str | None = None
    run_id: str | None = None


@dataclass
class PreparedAgent:
    loop: KernelLoop
    toolkit: Toolkit
    spec: AgentSpec
    state: AgentState
    call_context: AgentCallContext
    default_payload: dict[str, Any] = field(default_factory=dict)
    default_response_format: ResponseFormat | None = None
    default_max_iterations: int | None = None
    default_max_context_window_tokens: int | None = None
    default_on_tool_confirm: Callable[..., Any] | None = None
    default_on_human_input: Callable[..., Any] | None = None
    default_on_max_iterations: Callable[..., Any] | None = None
    run_hooks: list[RunHook] = field(default_factory=list)
    tool_runtime_plugins: list[Any] = field(default_factory=list)

    def _merge_payloads(self, *payloads: dict[str, Any] | None) -> dict[str, Any] | None:
        merged: dict[str, Any] = {}
        for payload in payloads:
            if isinstance(payload, dict):
                merged.update(copy.deepcopy(payload))
        return merged or None

    def _resolved_max_iterations(self) -> int:
        return max(1, int(self.call_context.max_iterations or self.default_max_iterations or 6))

    def _resolved_max_context_window_tokens(self) -> int | None:
        resolved = self.call_context.max_context_window_tokens
        if resolved is None:
            resolved = self.default_max_context_window_tokens
        if resolved is None:
            return None
        return max(0, int(resolved))

    def _resolved_response_format(self) -> ResponseFormat | None:
        return self.call_context.response_format or self.default_response_format

    def _resolved_on_tool_confirm(self) -> Callable[..., Any] | None:
        return self.call_context.on_tool_confirm or self.default_on_tool_confirm

    def _resolved_on_human_input(self) -> Callable[..., Any] | None:
        return self.call_context.on_human_input or self.default_on_human_input

    def _resolved_on_max_iterations(self) -> Callable[..., Any] | None:
        return self.call_context.on_max_iterations or self.default_on_max_iterations

    def _apply_run_hooks(self, result: KernelRunResult) -> KernelRunResult:
        current = result
        for hook in self.run_hooks:
            next_result = hook(current)
            if isinstance(next_result, KernelRunResult):
                current = next_result
        return current

    def run(self) -> KernelRunResult:
        result = self.loop.run(
            messages=copy.deepcopy(self.call_context.input_messages or []),
            payload=self._merge_payloads(self.default_payload, self.call_context.payload),
            response_format=self._resolved_response_format(),
            callback=self.call_context.callback,
            verbose=self.call_context.verbose,
            max_iterations=self._resolved_max_iterations(),
            previous_response_id=self.call_context.previous_response_id,
            on_tool_confirm=self._resolved_on_tool_confirm(),
            on_human_input=self._resolved_on_human_input(),
            on_max_iterations=self._resolved_on_max_iterations(),
            session_id=self.call_context.session_id,
            memory_namespace=self.call_context.memory_namespace,
            provider=self.spec.provider,
            model=self.spec.model,
            max_context_window_tokens=self._resolved_max_context_window_tokens(),
            toolkit=self.toolkit,
            run_id=self.call_context.run_id,
            tool_runtime_plugins=list(self.tool_runtime_plugins),
        )
        return self._apply_run_hooks(result)

    def resume_human_input(self) -> KernelRunResult:
        continuation_payload = None
        if isinstance(self.call_context.continuation, dict):
            raw_payload = self.call_context.continuation.get("payload")
            continuation_payload = raw_payload if isinstance(raw_payload, dict) else None
        result = self.loop.resume_human_input(
            conversation=copy.deepcopy(self.call_context.conversation or []),
            continuation=copy.deepcopy(self.call_context.continuation or {}),
            response=copy.deepcopy(self.call_context.response),
            payload=self._merge_payloads(self.default_payload, continuation_payload, self.call_context.payload),
            response_format=self._resolved_response_format(),
            callback=self.call_context.callback,
            verbose=self.call_context.verbose,
            on_tool_confirm=self._resolved_on_tool_confirm(),
            on_human_input=self._resolved_on_human_input(),
            on_max_iterations=self._resolved_on_max_iterations(),
            session_id=self.call_context.session_id,
            memory_namespace=self.call_context.memory_namespace,
            toolkit=self.toolkit,
            run_id=self.call_context.run_id,
            tool_runtime_plugins=list(self.tool_runtime_plugins),
        )
        return self._apply_run_hooks(result)


@dataclass
class AgentBuilder:
    agent: Any
    spec: AgentSpec
    state: AgentState
    call_context: AgentCallContext
    model_io_registry: ModelIOFactoryRegistry
    toolkit: Toolkit = field(default_factory=Toolkit)
    harnesses: list[Any] = field(default_factory=list)
    memory_runtime: KernelMemoryRuntime | None = None
    default_payload: dict[str, Any] = field(default_factory=dict)
    default_response_format: ResponseFormat | None = None
    default_max_iterations: int | None = None
    default_max_context_window_tokens: int | None = None
    default_on_tool_confirm: Callable[..., Any] | None = None
    default_on_human_input: Callable[..., Any] | None = None
    default_on_max_iterations: Callable[..., Any] | None = None
    run_hooks: list[RunHook] = field(default_factory=list)
    tool_runtime_plugins: list[Any] = field(default_factory=list)
    _model_io: ModelIO | None = None
    _model_io_factory: Callable[[AgentSpec, AgentCallContext], ModelIO] | None = None

    def add_tool(self, entry: Tool | Toolkit | Callable[..., Any]) -> None:
        if isinstance(entry, Toolkit):
            for tool_obj in entry.tools.values():
                self.toolkit.register(tool_obj)
            return
        if isinstance(entry, Tool):
            self.toolkit.register(entry)
            return
        if callable(entry):
            self.toolkit.register(entry)
            return
        raise TypeError(f"unsupported tool entry: {type(entry).__name__}")

    def add_harness(self, harness: Any) -> None:
        self.harnesses.append(harness)

    def attach_memory_runtime(self, memory_runtime: KernelMemoryRuntime) -> None:
        self.memory_runtime = memory_runtime

    def set_model_io(self, model_io: ModelIO) -> None:
        self._model_io = model_io

    def set_model_io_factory(self, factory: Callable[[AgentSpec, AgentCallContext], ModelIO]) -> None:
        self._model_io_factory = factory

    def add_run_hook(self, hook: RunHook) -> None:
        self.run_hooks.append(hook)

    def add_tool_runtime_plugin(self, plugin: Any) -> None:
        self.tool_runtime_plugins.append(plugin)

    def set_payload_defaults(self, payload: dict[str, Any]) -> None:
        self.default_payload.update(copy.deepcopy(payload))

    def set_response_format_default(self, response_format: ResponseFormat) -> None:
        self.default_response_format = response_format

    def set_max_iterations_default(self, max_iterations: int) -> None:
        self.default_max_iterations = int(max_iterations)

    def set_max_context_window_tokens_default(self, max_context_window_tokens: int) -> None:
        self.default_max_context_window_tokens = int(max_context_window_tokens)

    def set_on_tool_confirm_default(self, on_tool_confirm: Callable[..., Any]) -> None:
        self.default_on_tool_confirm = on_tool_confirm

    def set_on_human_input_default(self, on_human_input: Callable[..., Any]) -> None:
        self.default_on_human_input = on_human_input

    def set_on_max_iterations_default(self, on_max_iterations: Callable[..., Any]) -> None:
        self.default_on_max_iterations = on_max_iterations

    def _resolve_model_io(self) -> ModelIO:
        if self._model_io is not None:
            return self._model_io
        if self._model_io_factory is not None:
            return self._model_io_factory(self.spec, self.call_context)
        return self.model_io_registry.create(
            provider=self.spec.provider,
            model=self.spec.model,
            api_key=self.spec.api_key,
        )

    def _apply_allowed_tools_filter(self) -> None:
        if self.spec.allowed_tools is None:
            return
        allowed_names = [str(name).strip() for name in self.spec.allowed_tools if str(name).strip()]
        configured_names = list(self.toolkit.tools.keys())
        missing = [name for name in dict.fromkeys(allowed_names) if name not in self.toolkit.tools]
        if missing:
            raise ValueError(
                f"agent {self.spec.name!r} allowed_tools contains unknown tool names: {', '.join(missing)}"
            )
        allowed_name_set = set(allowed_names)
        self.toolkit.tools = {
            name: self.toolkit.tools[name]
            for name in configured_names
            if name in allowed_name_set
        }

    def build(self) -> PreparedAgent:
        self._apply_allowed_tools_filter()
        loop = KernelLoop(model_io=self._resolve_model_io())
        for harness in self.harnesses:
            loop.register_harness(harness)
        if self.memory_runtime is not None:
            loop.attach_memory(self.memory_runtime)
        return PreparedAgent(
            loop=loop,
            toolkit=self.toolkit,
            spec=self.spec,
            state=self.state,
            call_context=self.call_context,
            default_payload=copy.deepcopy(self.default_payload),
            default_response_format=self.default_response_format,
            default_max_iterations=self.default_max_iterations,
            default_max_context_window_tokens=self.default_max_context_window_tokens,
            default_on_tool_confirm=self.default_on_tool_confirm,
            default_on_human_input=self.default_on_human_input,
            default_on_max_iterations=self.default_on_max_iterations,
            run_hooks=list(self.run_hooks),
            tool_runtime_plugins=list(self.tool_runtime_plugins),
        )
