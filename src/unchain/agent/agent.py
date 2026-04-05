from __future__ import annotations

import copy
from typing import Any, Callable

from .modules.memory import MemoryModule
from ..kernel.types import KernelRunResult
from ..tools import Tool
from ..types.input import InputRequest, InputResponse
from .builder import AgentBuilder, AgentCallContext
from .model_io import ModelIOFactoryRegistry
from .spec import AgentSpec, AgentState


class Agent:
    def __init__(
        self,
        *,
        name: str,
        instructions: str = "",
        provider: str = "openai",
        model: str = "gpt-5",
        api_key: str | None = None,
        modules: tuple[Any, ...] = (),
        allowed_tools: tuple[str, ...] | None = None,
        model_io_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Agent name is required")
        self.spec = AgentSpec(
            name=name.strip(),
            instructions=instructions or "",
            provider=provider or "openai",
            model=model or "gpt-5",
            api_key=api_key,
            modules=tuple(modules or ()),
            allowed_tools=tuple(allowed_tools) if allowed_tools is not None else None,
        )
        self.state = AgentState()
        self._model_io_registry = ModelIOFactoryRegistry()
        self._model_io_factory = model_io_factory

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def instructions(self) -> str:
        return self.spec.instructions

    @property
    def provider(self) -> str:
        return self.spec.provider

    @property
    def model(self) -> str:
        return self.spec.model

    @property
    def allowed_tools(self) -> tuple[str, ...] | None:
        return self.spec.allowed_tools

    def _normalize_messages(self, messages: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(messages, str):
            normalized = [{"role": "user", "content": messages}]
        elif isinstance(messages, list):
            normalized = []
            for message in messages:
                if not isinstance(message, dict):
                    raise TypeError("messages must be a string or a list of dict messages")
                normalized.append(copy.deepcopy(message))
        else:
            raise TypeError("messages must be a string or a list of dict messages")
        if self.instructions:
            return [{"role": "system", "content": self.instructions}, *normalized]
        return normalized

    def _prepare(self, call_context: AgentCallContext):
        builder = AgentBuilder(
            agent=self,
            spec=self.spec,
            state=self.state,
            call_context=call_context,
            model_io_registry=self._model_io_registry,
        )
        if self._model_io_factory is not None:
            builder.set_model_io_factory(self._model_io_factory)
        for module in self.spec.modules:
            module.configure(builder)
        return builder.build()

    def clone(
        self,
        *,
        name: str | None = None,
        instructions: str | None = None,
        modules: tuple[Any, ...] | None = None,
        model: str | None = None,
        allowed_tools: tuple[str, ...] | None = None,
    ) -> "Agent":
        return Agent(
            name=name or self.name,
            instructions=self.instructions if instructions is None else instructions,
            provider=self.provider,
            model=self.model if model is None else model,
            api_key=self.spec.api_key,
            modules=tuple(self.spec.modules if modules is None else modules),
            allowed_tools=self.spec.allowed_tools if allowed_tools is None else tuple(allowed_tools),
            model_io_factory=self._model_io_factory,
        )

    def fork_for_subagent(
        self,
        *,
        subagent_name: str,
        mode: str,
        parent_name: str,
        lineage: list[str],
        task: str,
        instructions: str,
        expected_output: str,
        memory_policy: str,
        model: str | None = None,
        allowed_tools: tuple[str, ...] | None = None,
    ) -> "Agent":
        overlay = (
            f'You are subagent "{subagent_name}" created by parent "{parent_name}".\n'
            f"Mode: {mode}\n"
            f"Lineage: {' > '.join(lineage)}\n\n"
            "Only execute the delegated subtask.\n"
            "Do not ask the user directly for clarification. If clarification is required, return a concise structured clarification request via the runtime tools.\n"
            f"Delegated task:\n{task.strip()}\n\n"
        )
        if expected_output.strip():
            overlay += f"Expected output:\n{expected_output.strip()}\n\n"
        if instructions.strip():
            overlay += f"Extra instructions:\n{instructions.strip()}\n"
        modules = list(self.spec.modules)
        if memory_policy == "ephemeral":
            modules = [module for module in modules if not isinstance(module, MemoryModule)]
        return self.clone(
            name=subagent_name,
            instructions="\n\n".join(part for part in (self.instructions, overlay.strip()) if part.strip()),
            modules=tuple(modules),
            model=model,
            allowed_tools=allowed_tools,
        )

    def run(
        self,
        messages: str | list[dict[str, Any]],
        *,
        payload: dict[str, Any] | None = None,
        response_format: Any = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        max_iterations: int | None = None,
        max_context_window_tokens: int | None = None,
        previous_response_id: str | None = None,
        on_tool_confirm: Callable[..., Any] | None = None,
        on_human_input: Callable[..., Any] | None = None,
        on_max_iterations: Callable[..., Any] | None = None,
        on_input: Callable[[InputRequest], InputResponse] | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        run_id: str | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
    ) -> KernelRunResult:
        prepared = self._prepare(
            AgentCallContext(
                mode="run",
                input_messages=self._normalize_messages(messages),
                payload=copy.deepcopy(payload) if isinstance(payload, dict) else None,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                max_iterations=max_iterations,
                max_context_window_tokens=max_context_window_tokens,
                previous_response_id=previous_response_id,
                on_tool_confirm=on_tool_confirm,
                on_human_input=on_human_input,
                on_max_iterations=on_max_iterations,
                on_input=on_input,
                session_id=session_id,
                memory_namespace=memory_namespace,
                run_id=run_id,
                tool_runtime_config=copy.deepcopy(tool_runtime_config)
                if isinstance(tool_runtime_config, dict)
                else None,
            )
        )
        return prepared.run()

    def resume_human_input(
        self,
        *,
        conversation: list[dict[str, Any]],
        continuation: dict[str, Any],
        response: dict[str, Any] | Any,
        payload: dict[str, Any] | None = None,
        response_format: Any = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        on_tool_confirm: Callable[..., Any] | None = None,
        on_human_input: Callable[..., Any] | None = None,
        on_max_iterations: Callable[..., Any] | None = None,
        on_input: Callable[[InputRequest], InputResponse] | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
        run_id: str | None = None,
        tool_runtime_config: dict[str, Any] | None = None,
    ) -> KernelRunResult:
        prepared = self._prepare(
            AgentCallContext(
                mode="resume_human_input",
                conversation=copy.deepcopy(conversation),
                continuation=copy.deepcopy(continuation),
                response=copy.deepcopy(response),
                payload=copy.deepcopy(payload) if isinstance(payload, dict) else None,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                on_tool_confirm=on_tool_confirm,
                on_human_input=on_human_input,
                on_max_iterations=on_max_iterations,
                on_input=on_input,
                session_id=session_id,
                memory_namespace=memory_namespace,
                run_id=run_id,
                tool_runtime_config=copy.deepcopy(tool_runtime_config)
                if isinstance(tool_runtime_config, dict)
                else None,
            )
        )
        return prepared.resume_human_input()

    def _last_assistant_text(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages or []):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
        return ""

    def as_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        max_iterations: int | None = None,
    ) -> Tool:
        tool_name = name or self.name
        tool_description = description or f"Delegate the task to kernel agent '{self.name}'."

        def _delegate(task: str) -> dict[str, Any]:
            result = self.run(task, max_iterations=max_iterations)
            return {
                "agent": self.name,
                "status": result.status,
                "output": self._last_assistant_text(result.messages),
                "continuation": copy.deepcopy(result.continuation),
                "human_input_request": copy.deepcopy(result.human_input_request),
            }

        return Tool.from_callable(
            _delegate,
            name=tool_name,
            description=tool_description,
        )
