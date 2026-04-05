from __future__ import annotations

from typing import Any, Callable

from .models import HistoryPayloadOptimizer, ToolConfirmationPolicy, ToolExecutionContext, ToolParameter, ToolPromptSpec
from .tool import Tool


def tool(
    __func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    func: Callable[..., Any] | None = None,
    parameters: list[ToolParameter | dict[str, Any]] | None = None,
    observe: bool = False,
    requires_confirmation: bool = False,
    render_component: dict[str, Any] | None = None,
    confirmation_resolver: (
        Callable[[dict[str, Any], ToolExecutionContext | None], ToolConfirmationPolicy | bool | dict[str, Any] | None]
        | None
    ) = None,
    prompt_spec: ToolPromptSpec | dict[str, Any] | None = None,
    history_arguments_optimizer: HistoryPayloadOptimizer | None = None,
    history_result_optimizer: HistoryPayloadOptimizer | None = None,
):
    target = func
    if callable(__func) and target is None:
        target = __func

    if target is not None:
        return Tool.from_callable(
            target,
            name=name,
            description=description,
            parameters=parameters,
            observe=observe,
            requires_confirmation=requires_confirmation,
            render_component=render_component,
            confirmation_resolver=confirmation_resolver,
            prompt_spec=prompt_spec,
            history_arguments_optimizer=history_arguments_optimizer,
            history_result_optimizer=history_result_optimizer,
        )

    def decorator(inner: Callable[..., Any]) -> Tool:
        return Tool.from_callable(
            inner,
            name=name,
            description=description,
            parameters=parameters,
            observe=observe,
            requires_confirmation=requires_confirmation,
            render_component=render_component,
            confirmation_resolver=confirmation_resolver,
            prompt_spec=prompt_spec,
            history_arguments_optimizer=history_arguments_optimizer,
            history_result_optimizer=history_result_optimizer,
        )

    return decorator


__all__ = ["tool"]
