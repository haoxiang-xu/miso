"""Terminal renderer for Unchain's flat event stream.

A drop-in callback that turns the kernel/provider/tool event stream into a
colored, real-time terminal view. Pass an instance directly to
``Agent.run(callback=...)`` — instances are callable, dispatching by event
type to a method table.

This module depends on ``rich``. Install it via the ``render`` extra:

    pip install unchain[render]

Quickstart:

    from unchain import Agent
    from unchain.render import TerminalRenderer

    renderer = TerminalRenderer()
    agent = Agent(...)
    agent.run("translate this", callback=renderer)
    renderer.print_summary()        # optional end-of-run block

To customize tool result one-liners, subclass and override
``render_tool_result_sketch``. The default surfaces ``error`` fields and
falls back to listing top-level keys.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from rich.console import Console
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "unchain.render requires the 'render' extra. "
        "Install with: pip install unchain[render]"
    ) from exc

from ._colors import (
    DEFAULT_STREAM_COLOR,
    DEFAULT_TOOL_COLOR,
    color_for_tool,
)
from ._format import arg_keys, fallback_tool_result_sketch, truncate


@dataclass
class RenderTotals:
    """Aggregate counters surfaced by ``TerminalRenderer.totals``."""

    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    consumed_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)


class TerminalRenderer:
    """Single-callback terminal renderer for an Unchain run.

    Pass ``renderer`` (the instance — instances are callable) as the
    ``callback`` argument to ``Agent.run`` or ``KernelLoop.run``. Each event
    is dispatched by ``event["type"]`` to a method on this class; unknown
    types are rendered as a dim one-liner so additions to Unchain's event
    schema never silently disappear.
    """

    # ------------------------------------------------------------------
    # Construction

    def __init__(
        self,
        *,
        console: Console | None = None,
        tool_colors: dict[str, str] | None = None,
        show_reasoning: bool = True,
        show_token_deltas: bool = True,
        show_request_messages: bool = False,
        show_observations: bool = False,
        show_memory_events: bool = False,
        truncate_text: int = 120,
    ) -> None:
        self.console: Console = console or Console()
        self._tool_colors: dict[str, str] = dict(tool_colors or {})
        self._show_reasoning = show_reasoning
        self._show_token_deltas = show_token_deltas
        self._show_request_messages = show_request_messages
        self._show_observations = show_observations
        self._show_memory_events = show_memory_events
        self._truncate_text = int(truncate_text)

        self.totals = RenderTotals()
        self._tool_starts: dict[str, float] = {}
        self._active_tool: str | None = None
        self._stream_open: bool = False
        self._run_started_at: float | None = None

    # ------------------------------------------------------------------
    # Deepcopy safety
    #
    # KernelLoop deep-copies the dispatch-phase event, which can drag bound
    # callbacks (and through them, this renderer's Console with its internal
    # RLocks) into copy.deepcopy. The renderer is intentionally a per-run
    # singleton, so returning self is correct: every "copy" should mutate
    # the same totals and write to the same console.

    def __deepcopy__(self, memo: dict[int, Any]) -> "TerminalRenderer":
        return self

    # ------------------------------------------------------------------
    # Public API

    def __call__(self, event: dict[str, Any]) -> None:
        """Callback entrypoint. Pass ``renderer`` directly to ``Agent.run``."""

        ev_type = event.get("type")
        handler: Callable[[dict[str, Any]], None] | None = (
            _HANDLERS.get(ev_type) if isinstance(ev_type, str) else None
        )
        try:
            if handler is None:
                self._on_unknown(event)
                return
            handler(self, event)
        except Exception as exc:  # noqa: BLE001 — never break a run for a render error
            self.console.print(
                f"[red]renderer error[/] [{type(exc).__name__}]: {exc} "
                f"(event_type={ev_type!r})"
            )

    def print_summary(self) -> None:
        """Render an end-of-run summary block. Safe to call once after run."""

        elapsed_ms = self._elapsed_ms()
        self.console.rule("run summary")
        self.console.print(
            f"elapsed: {elapsed_ms} ms   iterations: {self.totals.iterations}"
        )
        self.console.print(
            "tokens (in / out / cache_read / cache_create / consumed): "
            f"{self.totals.input_tokens} / {self.totals.output_tokens} / "
            f"{self.totals.cache_read_input_tokens} / "
            f"{self.totals.cache_creation_input_tokens} / "
            f"{self.totals.consumed_tokens}"
        )
        if self.totals.tool_calls:
            counts = ", ".join(
                f"{name}={n}" for name, n in self.totals.tool_calls.items()
            )
            self.console.print(f"tool calls: {counts}")

    # ------------------------------------------------------------------
    # Extension hook

    def render_tool_result_sketch(self, tool_name: str, result: Any) -> str:
        """One-line summary printed next to the tool_result tick.

        Default implementation delegates to ``fallback_tool_result_sketch``,
        which surfaces ``error`` fields and otherwise lists top-level keys.
        Subclass and override to produce richer summaries (e.g.
        ``"chunks=5"``, ``"id=c1 tgt='...'"``).
        """

        return fallback_tool_result_sketch(result)

    # ------------------------------------------------------------------
    # Internals — stream management

    def _color_for(self, tool_name: str | None) -> str:
        if not tool_name:
            return DEFAULT_TOOL_COLOR
        return color_for_tool(tool_name, overrides=self._tool_colors)

    def _close_stream_line(self) -> None:
        if self._stream_open:
            self.console.print()
            self._stream_open = False

    def _elapsed_ms(self) -> int:
        if self._run_started_at is None:
            return 0
        return int((time.time() - self._run_started_at) * 1000)

    # ------------------------------------------------------------------
    # Provider streaming events

    def _on_token_delta(self, event: dict[str, Any]) -> None:
        if not self._show_token_deltas:
            return
        delta = str(event.get("delta") or "")
        if not delta:
            return
        color = self._color_for(self._active_tool) or DEFAULT_STREAM_COLOR
        self.console.print(
            delta, end="", style=color, soft_wrap=True, highlight=False
        )
        self._stream_open = True

    def _on_reasoning(self, event: dict[str, Any]) -> None:
        if not self._show_reasoning:
            return
        delta = str(event.get("delta") or "")
        if not delta:
            return
        color = self._color_for(self._active_tool) or DEFAULT_STREAM_COLOR
        self.console.print(
            delta, end="", style=f"dim {color}", soft_wrap=True, highlight=False
        )
        self._stream_open = True

    def _on_request_messages(self, event: dict[str, Any]) -> None:
        if not self._show_request_messages:
            return
        self._close_stream_line()
        n = len(event.get("messages") or [])
        self.console.print(f"[dim]request_messages: messages_len={n}[/]")

    def _on_previous_response_id_fallback(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        self.console.print(
            f"[dim]previous_response_id_fallback: {event.get('reason', '')}[/]"
        )

    # ------------------------------------------------------------------
    # Kernel loop events

    def _on_run_started(self, event: dict[str, Any]) -> None:
        self._run_started_at = time.time()
        provider = event.get("provider") or ""
        model = event.get("model") or ""
        self.console.rule("unchain run")
        self.console.print(
            f"[bold]provider[/]: {provider}    [bold]model[/]: {model}"
        )

    def _on_iteration_started(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        iteration = int(event.get("iteration") or 0)
        self.totals.iterations = max(self.totals.iterations, iteration + 1)
        self.console.print(f"[dim]── iteration {iteration} ──[/]")

    def _on_response_received(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        bundle = event.get("bundle") or {}
        last_in = int(bundle.get("last_turn_input_tokens") or 0)
        last_out = int(bundle.get("last_turn_output_tokens") or 0)
        self.totals.input_tokens = int(
            bundle.get("input_tokens") or self.totals.input_tokens
        )
        self.totals.output_tokens = int(
            bundle.get("output_tokens") or self.totals.output_tokens
        )
        self.totals.consumed_tokens = int(
            bundle.get("consumed_tokens") or self.totals.consumed_tokens
        )
        self.totals.cache_read_input_tokens = int(
            bundle.get("cache_read_input_tokens")
            or self.totals.cache_read_input_tokens
        )
        self.totals.cache_creation_input_tokens = int(
            bundle.get("cache_creation_input_tokens")
            or self.totals.cache_creation_input_tokens
        )
        has_tools = bool(event.get("has_tool_calls"))
        self.console.print(
            f"  [dim]turn tokens in/out = {last_in}/{last_out}; "
            f"has_tool_calls={has_tools}[/]"
        )

    def _on_iteration_completed(self, event: dict[str, Any]) -> None:
        self._close_stream_line()

    def _on_final_message(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        content = str(event.get("content") or "")
        preview = truncate(content, self._truncate_text)
        self.console.print(f"[bold]final assistant message[/]: {preview!r}")

    def _on_run_completed(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        status = str(event.get("status") or "")
        self.console.print(f"[bold green]✓ run completed[/] (status={status})")

    def _on_run_max_iterations(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        iteration = int(event.get("iteration") or 0)
        self.console.print(
            f"[bold red]⚠ run hit max_iterations[/] at iteration {iteration}"
        )

    def _on_memory_event(self, event: dict[str, Any]) -> None:
        if not self._show_memory_events:
            return
        self._close_stream_line()
        ev_type = event.get("type")
        self.console.print(f"[dim]{ev_type}[/]")

    # ------------------------------------------------------------------
    # Tool execution events

    def _on_tool_call(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        tool_name = str(event.get("tool_name") or "")
        call_id = str(event.get("call_id") or "")
        arguments = event.get("arguments") or {}
        color = self._color_for(tool_name)
        self._tool_starts[call_id or tool_name] = time.time()
        self._active_tool = tool_name
        self.totals.tool_calls[tool_name] = (
            self.totals.tool_calls.get(tool_name, 0) + 1
        )
        keys = arg_keys(arguments)
        self.console.print(
            f"[{color}]▶ {tool_name}[/] [dim](call_id={call_id})[/]  arg-keys={keys}"
        )

    def _on_tool_result(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        tool_name = str(event.get("tool_name") or "")
        call_id = str(event.get("call_id") or "")
        result = event.get("result") or {}
        color = self._color_for(tool_name)
        started = self._tool_starts.pop(call_id or tool_name, None)
        duration_ms = int((time.time() - started) * 1000) if started else None
        sketch = self.render_tool_result_sketch(tool_name, result)
        dur_label = f"{duration_ms} ms" if duration_ms is not None else "—"
        # Drop the trailing double-space when sketch is empty.
        sketch_part = f"  {sketch}" if sketch else ""
        self.console.print(
            f"[{color}]✓ {tool_name}[/] [dim](call_id={call_id})[/]"
            f"{sketch_part}  [dim]({dur_label})[/]"
        )

    def _on_tool_confirmed(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        tool_name = str(event.get("tool_name") or "")
        color = self._color_for(tool_name)
        self.console.print(f"[{color}]🔓 {tool_name} confirmed[/]")

    def _on_tool_denied(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        tool_name = str(event.get("tool_name") or "")
        color = self._color_for(tool_name)
        self.console.print(f"[{color}]🔒 {tool_name} denied[/]")

    def _on_human_input_requested(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        prompt = str(event.get("prompt") or event.get("question") or "")
        preview = truncate(prompt, self._truncate_text)
        self.console.print(f"[bold yellow]⏸ human input requested[/]: {preview}")

    def _on_observation(self, event: dict[str, Any]) -> None:
        if not self._show_observations:
            return
        self._close_stream_line()
        text = str(event.get("text") or event.get("observation") or "")
        preview = truncate(text, self._truncate_text)
        self.console.print(f"[dim]obs:[/] {preview}")

    # ------------------------------------------------------------------
    # Fallback

    def _on_unknown(self, event: dict[str, Any]) -> None:
        self._close_stream_line()
        ev_type = event.get("type")
        keys = [k for k in event.keys() if k not in {"type", "run_id", "iteration"}]
        self.console.print(f"[dim]<{ev_type}> keys={keys}[/]")


# Method dispatch table. Defined at module level so subclasses don't need to
# re-declare it; a subclass overriding e.g. ``_on_tool_call`` still gets
# routed correctly because we look up the method on the instance.
_HANDLERS: dict[str, Callable[[TerminalRenderer, dict[str, Any]], None]] = {
    # Provider streaming
    "token_delta": TerminalRenderer._on_token_delta,
    "reasoning": TerminalRenderer._on_reasoning,
    "request_messages": TerminalRenderer._on_request_messages,
    "previous_response_id_fallback": TerminalRenderer._on_previous_response_id_fallback,
    # Kernel loop
    "run_started": TerminalRenderer._on_run_started,
    "iteration_started": TerminalRenderer._on_iteration_started,
    "response_received": TerminalRenderer._on_response_received,
    "iteration_completed": TerminalRenderer._on_iteration_completed,
    "final_message": TerminalRenderer._on_final_message,
    "run_completed": TerminalRenderer._on_run_completed,
    "run_max_iterations": TerminalRenderer._on_run_max_iterations,
    "memory_prepare": TerminalRenderer._on_memory_event,
    "memory_commit": TerminalRenderer._on_memory_event,
    # Tool execution
    "tool_call": TerminalRenderer._on_tool_call,
    "tool_result": TerminalRenderer._on_tool_result,
    "tool_confirmed": TerminalRenderer._on_tool_confirmed,
    "tool_denied": TerminalRenderer._on_tool_denied,
    "human_input_requested": TerminalRenderer._on_human_input_requested,
    "observation": TerminalRenderer._on_observation,
}


__all__ = ["TerminalRenderer", "RenderTotals"]
