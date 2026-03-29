from __future__ import annotations

import copy
from typing import Any, Callable

from ...tools import Tool
from ..types import KernelRunResult
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
        session_id: str | None = None,
        memory_namespace: str | None = None,
        run_id: str | None = None,
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
                session_id=session_id,
                memory_namespace=memory_namespace,
                run_id=run_id,
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
        session_id: str | None = None,
        memory_namespace: str | None = None,
        run_id: str | None = None,
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
                session_id=session_id,
                memory_namespace=memory_namespace,
                run_id=run_id,
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
