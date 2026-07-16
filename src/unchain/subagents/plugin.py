from __future__ import annotations

import copy
import inspect
import json
import math
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..execution import ExecutionGuard, _borrow_execution_guard
from ..tools.common import emit_loop_event
from ..tools.runtime import ToolRuntimeOutcome, ToolRuntimePlugin
from ..kernel.types import ToolCall
from .communication import AgentCommunicationRuntime, AgentThreadRecord
from .executor import SubagentExecutor
from .types import SubagentPolicy, SubagentResult, SubagentState, SubagentTemplate

if TYPE_CHECKING:
    from ..agent.agent import Agent as KernelAgent


_SUBAGENT_TOOL_NAMES = {"delegate_to_subagent", "handoff_to_subagent", "spawn_worker_batch"}
_COMMUNICATION_TOOL_NAMES = {
    "spawn_agent_thread",
    "send_agent_message",
    "wait_agent_messages",
    "close_agent_thread",
    "write_agent_board",
    "read_agent_board",
    "return_handoff_to_subagent",
    "return_to_parent",
}
_TERMINAL_THREAD_STATUSES = {
    "completed",
    "failed",
    "closed",
    "max_iterations",
    "needs_clarification",
    "awaiting_human_input",
}
_WAITING_RUN_STATUSES = {
    "awaiting_human_input",
    "awaiting_interaction",
}


class _ChildRunError(RuntimeError):
    def __init__(self, original: Exception, subagent_state: dict[str, Any]):
        super().__init__(str(original))
        self.original = original
        self.subagent_state = copy.deepcopy(subagent_state)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", str(value or "").strip().lower())
    slug = re.sub(r"\.+", ".", slug).strip(".")
    return slug or "subagent"


def _parse_arguments(arguments: dict[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return copy.deepcopy(arguments)
    if isinstance(arguments, str) and arguments.strip():
        return json.loads(arguments)
    return {}


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _extract_return_to_parent_payload(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue

        output = message.get("output")
        if isinstance(output, str) and output.strip():
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("mode") == "return_to_parent":
                return copy.deepcopy(parsed)

        content = message.get("content")
        if isinstance(content, str) and content.strip():
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("mode") == "return_to_parent":
                return copy.deepcopy(parsed)
        if isinstance(content, list):
            for block in reversed(content):
                if not isinstance(block, dict):
                    continue
                for key in ("text", "content"):
                    text = block.get(key)
                    if not isinstance(text, str) or not text.strip():
                        continue
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict) and parsed.get("mode") == "return_to_parent":
                        return copy.deepcopy(parsed)
    return None


def _matches_runtime_tool_call(raw: dict[str, Any], *, call_id: str, tool_name: str) -> bool:
    raw_call_id = str(raw.get("id") or raw.get("call_id") or raw.get("tool_use_id") or "")
    raw_name = str(raw.get("name") or "")
    if call_id and raw_call_id == call_id:
        return True
    return bool(tool_name and raw_name == tool_name)


def _sanitize_handoff_messages(
    messages: list[dict[str, Any]],
    *,
    tool_call: ToolCall,
) -> list[dict[str, Any]]:
    """Remove the current runtime tool call from carried handoff context.

    Handoff finishes the parent run without emitting a provider-native tool
    result message. If the current handoff tool call is left in the transcript,
    child providers such as Anthropic may reject the carried context as an
    orphaned / unfinished tool-use turn.
    """

    call_id = str(tool_call.call_id or "")
    tool_name = str(tool_call.name or "")
    sanitized: list[dict[str, Any]] = []

    for message in messages or []:
        if not isinstance(message, dict):
            continue

        msg = copy.deepcopy(message)

        if str(msg.get("type") or "") == "function_call" and _matches_runtime_tool_call(
            msg,
            call_id=call_id,
            tool_name=tool_name,
        ):
            continue

        removed_current_tool_call = False
        raw_tool_calls = msg.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            kept_tool_calls = [
                copy.deepcopy(raw_tool_call)
                for raw_tool_call in raw_tool_calls
                if not (
                    isinstance(raw_tool_call, dict)
                    and _matches_runtime_tool_call(
                        raw_tool_call,
                        call_id=call_id,
                        tool_name=tool_name,
                    )
                )
            ]
            removed_current_tool_call = len(kept_tool_calls) != len(raw_tool_calls)
            if kept_tool_calls:
                msg["tool_calls"] = kept_tool_calls
            else:
                msg.pop("tool_calls", None)

        content = msg.get("content")
        if isinstance(content, list):
            kept_blocks: list[Any] = []
            for block in content:
                if not isinstance(block, dict):
                    kept_blocks.append(copy.deepcopy(block))
                    continue
                if str(block.get("type") or "") == "tool_use" and _matches_runtime_tool_call(
                    block,
                    call_id=call_id,
                    tool_name=tool_name,
                ):
                    removed_current_tool_call = True
                    continue
                kept_blocks.append(copy.deepcopy(block))

            if kept_blocks:
                msg["content"] = kept_blocks
            else:
                msg["content"] = ""

        if removed_current_tool_call:
            continue

        has_content = msg.get("content") not in ("", [], None)
        has_tool_calls = bool(msg.get("tool_calls"))
        if has_content or has_tool_calls or msg.get("role") != "assistant":
            sanitized.append(msg)

    return sanitized


@dataclass
class SubagentToolPlugin(ToolRuntimePlugin):
    parent_agent: "KernelAgent"
    templates: tuple[SubagentTemplate, ...]
    policy: SubagentPolicy
    executor: SubagentExecutor

    @property
    def template_map(self) -> dict[str, SubagentTemplate]:
        return {template.name: template for template in self.templates}

    @property
    def communication_runtime(self) -> AgentCommunicationRuntime:
        return AgentCommunicationRuntime(self.policy)

    def can_handle(self, *, tool_call: ToolCall, context) -> bool:
        if tool_call.name not in _SUBAGENT_TOOL_NAMES | _COMMUNICATION_TOOL_NAMES:
            return False
        toolkit = getattr(context, "toolkit", None)
        if toolkit is None or not hasattr(toolkit, "get"):
            return False
        return toolkit.get(tool_call.name) is not None

    def execute(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        try:
            if tool_call.name == "delegate_to_subagent":
                return self._delegate(tool_call=tool_call, context=context)
            if tool_call.name == "handoff_to_subagent":
                return self._handoff(tool_call=tool_call, context=context)
            if tool_call.name == "spawn_worker_batch":
                return self._worker_batch(tool_call=tool_call, context=context)
            if tool_call.name == "spawn_agent_thread":
                return self._spawn_agent_thread(tool_call=tool_call, context=context)
            if tool_call.name == "send_agent_message":
                return self._send_agent_message(tool_call=tool_call, context=context)
            if tool_call.name == "wait_agent_messages":
                return self._wait_agent_messages(tool_call=tool_call, context=context)
            if tool_call.name == "close_agent_thread":
                return self._close_agent_thread(tool_call=tool_call, context=context)
            if tool_call.name == "write_agent_board":
                return self._write_agent_board(tool_call=tool_call, context=context)
            if tool_call.name == "read_agent_board":
                return self._read_agent_board(tool_call=tool_call, context=context)
            if tool_call.name == "return_handoff_to_subagent":
                return self._return_handoff_to_subagent(tool_call=tool_call, context=context)
            if tool_call.name == "return_to_parent":
                return self._return_to_parent(tool_call=tool_call, context=context)
            return ToolRuntimeOutcome(handled=False)
        except Exception as exc:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "error": str(exc),
                    "tool": tool_call.name,
                },
            )

    def _ensure_state(self, context) -> SubagentState:
        current = getattr(context.state, "subagent_state", None)
        state = SubagentState.from_raw(current)
        if not state.root_agent_id:
            state.root_agent_id = self.parent_agent.name
        if not state.active_agent_id:
            state.active_agent_id = self.parent_agent.name
        if not state.active_lineage:
            state.active_lineage = [state.root_agent_id]
        return state

    def _next_subagent_identity(
        self,
        *,
        state: SubagentState,
        target: str,
        mode: str,
    ) -> tuple[str, list[str], SubagentState]:
        current = state.copy()
        parent_id = current.active_agent_id or self.parent_agent.name
        parent_lineage = list(current.active_lineage or [current.root_agent_id or self.parent_agent.name])
        next_depth = len(parent_lineage)
        if next_depth > int(self.policy.max_depth):
            raise ValueError(f"subagent max_depth exceeded: attempted depth {next_depth} > {self.policy.max_depth}")
        key = parent_id
        current_children = int(current.lineage_counters.get(key, 0))
        if current_children >= int(self.policy.max_children_per_parent):
            raise ValueError(
                "subagent max_children_per_parent exceeded: "
                f"attempted child {current_children + 1} > {self.policy.max_children_per_parent}"
            )
        total_created = sum(int(value) for value in current.lineage_counters.values())
        if total_created >= int(self.policy.max_total_subagents):
            raise ValueError(
                "subagent max_total_subagents exceeded: "
                f"attempted child {total_created + 1} > {self.policy.max_total_subagents}"
            )
        next_index = current_children + 1
        current.lineage_counters[key] = next_index
        current.spawn_stats[mode] = int(current.spawn_stats.get(mode, 0)) + 1
        child_id = f"{parent_id}.{_slug(target)}.{next_index}"
        lineage = [*parent_lineage, child_id]
        return child_id, lineage, current

    def _resolve_template(self, target: str, *, mode: str) -> SubagentTemplate | None:
        template = self.template_map.get(str(target or "").strip())
        if template is None:
            return None
        if not template.supports_mode(mode):  # type: ignore[arg-type]
            raise ValueError(f"subagent template {template.name!r} does not support mode={mode!r}")
        return template

    def _build_subagent(
        self,
        *,
        template: SubagentTemplate | None,
        child_id: str,
        lineage: list[str],
        mode: str,
        target: str,
        task: str,
        instructions: str,
        expected_output: str,
    ) -> tuple["KernelAgent", str, str | None]:
        memory_policy = template.memory_policy if template is not None else ("ephemeral" if mode != "handoff" else "scoped_persistent")
        if template is not None:
            base_agent = template.agent or self.parent_agent
            child = base_agent.fork_for_subagent(
                subagent_name=child_id,
                mode=mode,
                parent_name=self.parent_agent.name,
                lineage=lineage,
                task=task,
                instructions=instructions,
                expected_output=expected_output,
                memory_policy=memory_policy,
                model=template.model,
                allowed_tools=template.allowed_tools,
                missing_tool_policy="warn_skip",
            )
            return child, memory_policy, template.name
        if mode == "handoff" and self.policy.handoff_requires_template:
            raise ValueError("handoff_to_subagent requires a registered template")
        if mode == "delegate" and not self.policy.allow_dynamic_delegate:
            raise ValueError("dynamic delegate_to_subagent is disabled by policy")
        if mode == "worker" and not self.policy.allow_dynamic_workers:
            raise ValueError("dynamic worker spawning is disabled by policy")
        child = self.parent_agent.fork_for_subagent(
            subagent_name=child_id,
            mode=mode,
            parent_name=self.parent_agent.name,
            lineage=lineage,
            task=task,
            instructions=instructions,
            expected_output=expected_output,
            memory_policy=memory_policy,
            missing_tool_policy="warn_skip",
        )
        return child, memory_policy, None

    def _build_child_run_id(self, *, session_id: str, child_id: str) -> str:
        return f"{session_id}:{child_id}:{uuid.uuid4()}"

    def _merge_result_subagent_state(self, state: SubagentState, result: SubagentResult) -> SubagentState:
        if not result.subagent_state:
            return state
        return state.merged(result.subagent_state)

    def _subagent_state_from_child_exception(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, _ChildRunError):
            return copy.deepcopy(exc.subagent_state)
        return {}

    def _merge_child_exception_subagent_state(self, state: SubagentState, exc: Exception) -> SubagentState:
        delta = self._subagent_state_from_child_exception(exc)
        if not delta:
            return state
        return state.merged(delta)

    def _run_child(
        self,
        *,
        agent: "KernelAgent",
        mode: str,
        child_id: str,
        lineage: list[str],
        template_name: str | None,
        session_id: str,
        memory_namespace: str,
        input_messages: str | list[dict[str, Any]],
        max_iterations: int,
        child_run_id: str = "",
        callback: Any = None,
        on_tool_confirm: Any = None,
        on_human_input: Any = None,
        on_max_iterations: Any = None,
        execution_guard: ExecutionGuard | None = None,
    ) -> SubagentResult:
        if not child_run_id:
            child_run_id = self._build_child_run_id(
                session_id=session_id,
                child_id=child_id,
            )
        captured_state = SubagentState()
        captured_item_ids: set[str] = set()

        def _child_callback(event: dict[str, Any]) -> None:
            if execution_guard is not None:
                execution_guard.assert_active()
            if isinstance(event, dict):
                if event.get("type") == "human_input_requested":
                    return None
                result_payload = event.get("result")
                if (
                    event.get("type") == "tool_result"
                    and event.get("tool_name") == "write_agent_board"
                    and isinstance(result_payload, dict)
                    and result_payload.get("mode") == "agent_board_write"
                    and result_payload.get("status") == "written"
                    and isinstance(result_payload.get("item"), dict)
                ):
                    item = copy.deepcopy(result_payload["item"])
                    item_id = str(item.get("item_id") or "")
                    if not item_id or item_id not in captured_item_ids:
                        if item_id:
                            captured_item_ids.add(item_id)
                        board_id = str(item.get("board_id") or "default")
                        captured_state.blackboards.setdefault(board_id, []).append(item)
            if callable(callback):
                callback(event)

        def _captured_delta() -> dict[str, Any]:
            return {"blackboards": copy.deepcopy(captured_state.blackboards)} if captured_state.blackboards else {}

        child_execution_guard = (
            _borrow_execution_guard(execution_guard, session_id=session_id)
            if execution_guard is not None
            else None
        )
        guarded_run_kwargs: dict[str, Any] = {}
        if child_execution_guard is not None:
            try:
                run_parameters = inspect.signature(agent.run).parameters.values()
            except (TypeError, ValueError):
                run_parameters = ()
            if any(
                (
                    parameter.name == "_execution_guard"
                    and parameter.kind != inspect.Parameter.POSITIONAL_ONLY
                )
                or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in run_parameters
            ):
                guarded_run_kwargs["_execution_guard"] = child_execution_guard
            child_execution_guard.assert_active()
        try:
            result = agent.run(
                input_messages,
                session_id=session_id,
                memory_namespace=memory_namespace,
                max_iterations=max_iterations,
                callback=_child_callback,
                on_tool_confirm=on_tool_confirm,
                on_human_input=on_human_input,
                on_max_iterations=on_max_iterations,
                run_id=child_run_id,
                **guarded_run_kwargs,
            )
            if child_execution_guard is not None:
                if (
                    result.status in _WAITING_RUN_STATUSES
                    and execution_guard is not None
                ):
                    execution_guard.assert_active()
                else:
                    child_execution_guard.assert_active()
        except Exception as exc:
            raise _ChildRunError(exc, _captured_delta()) from exc
        output = _last_assistant_text(result.messages)
        captured_delta = _captured_delta()
        if result.status == "awaiting_human_input":
            return SubagentResult(
                mode=mode,
                agent_name=agent.name,
                template_name=template_name,
                status="needs_clarification",
                output="",
                summary="clarification required",
                messages=[],
                lineage=lineage,
                clarification_request=copy.deepcopy(result.human_input_request),
                subagent_state=captured_delta,
            )
        return SubagentResult(
            mode=mode,
            agent_name=agent.name,
            template_name=template_name,
            status=result.status,
            output=output,
            summary=output,
            messages=copy.deepcopy(result.messages),
            lineage=lineage,
            subagent_state=captured_delta,
        )

    def _render_result(
        self,
        *,
        result: SubagentResult,
        output_mode: str,
        template_name: str | None,
    ) -> dict[str, Any]:
        payload = result.to_dict()
        payload["template_name"] = template_name
        if output_mode != "full_trace":
            payload["messages"] = []
        if output_mode == "last_message":
            payload["summary"] = result.output
        return payload

    def _emit_subagent_event(
        self,
        context,
        event_type: str,
        *,
        subagent_id: str,
        parent_id: str,
        mode: str,
        template: str | None,
        lineage: list[str],
        batch_id: str | None = None,
        **extra: Any,
    ) -> None:
        emit_loop_event(
            context.loop,
            context.callback,
            event_type,
            context.run_id,
            iteration=context.iteration,
            root_agent=self._ensure_state(context).root_agent_id or self.parent_agent.name,
            root_run_id=context.run_id,
            subagent_id=subagent_id,
            parent_id=parent_id,
            mode=mode,
            template=template,
            lineage=list(lineage),
            batch_id=batch_id,
            **extra,
        )

    def _spawn_agent_thread(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        target = str(args.get("target") or "").strip()
        task = str(args.get("task") or "").strip()
        instructions = str(args.get("instructions") or "").strip()
        expected_output = str(args.get("expected_output") or "").strip()
        context_mode = str(args.get("context_mode") or "none").strip() or "none"
        background = bool(args.get("background", False))
        if not target:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "spawn_agent_thread requires target"})
        if not task:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "spawn_agent_thread requires task"})

        state = self._ensure_state(context)
        child_id, lineage, next_state = self._next_subagent_identity(state=state, target=target, mode="delegate")
        template = self._resolve_template(target, mode="delegate")
        child, memory_policy, template_name = self._build_subagent(
            template=template,
            child_id=child_id,
            lineage=lineage,
            mode="delegate",
            target=target,
            task=task,
            instructions=instructions,
            expected_output=expected_output,
        )
        session_id = f"{context.session_id or context.run_id}:{child_id}"
        memory_namespace = f"{context.memory_namespace or context.session_id or context.run_id}:{child_id}"
        scoped_memory_namespace = memory_namespace if memory_policy == "scoped_persistent" else ""
        parent_id = state.active_agent_id or self.parent_agent.name
        child_run_id = self._build_child_run_id(
            session_id=context.session_id or context.run_id,
            child_id=child_id,
        )
        running_record = AgentThreadRecord(
            thread_id=child_id,
            agent_id=child_id,
            parent_agent_id=parent_id,
            target=target,
            template_name=template_name,
            mode="thread",
            status="running",
            session_id=session_id,
            memory_namespace=scoped_memory_namespace,
            lineage=tuple(lineage),
            created_iteration=int(context.iteration),
            last_activity_iteration=int(context.iteration),
            context_mode=context_mode,
            instructions=instructions,
            expected_output=expected_output,
        )
        try:
            threaded_state = self.communication_runtime.upsert_thread(next_state, running_record)
        except ValueError as exc:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": str(exc)})

        self._emit_subagent_event(
            context,
            "agent_thread_spawned",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="thread",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            thread_id=child_id,
            background=background,
        )
        try:
            result = self._run_child(
                agent=child,
                mode="thread",
                child_id=child_id,
                lineage=lineage,
                template_name=template_name,
                session_id=session_id,
                memory_namespace=scoped_memory_namespace,
                input_messages=task,
                max_iterations=int(context.event.get("max_iterations") or 6),
                child_run_id=child_run_id,
                callback=context.callback,
                on_tool_confirm=context.event.get("on_tool_confirm"),
                on_human_input=context.event.get("on_human_input"),
                on_max_iterations=context.event.get("on_max_iterations"),
                execution_guard=getattr(context, "execution_guard", None),
            )
        except Exception as exc:
            failed_record = AgentThreadRecord(
                thread_id=child_id,
                agent_id=child_id,
                parent_agent_id=parent_id,
                target=target,
                template_name=template_name,
                mode="thread",
                status="failed",
                session_id=session_id,
                memory_namespace=scoped_memory_namespace,
                lineage=tuple(lineage),
                created_iteration=int(context.iteration),
                last_activity_iteration=int(context.iteration),
                context_mode=context_mode,
                instructions=instructions,
                expected_output=expected_output,
                close_reason=str(exc),
            )
            failed_state = self.communication_runtime.upsert_thread(threaded_state, failed_record)
            failed_state = self._merge_child_exception_subagent_state(failed_state, exc)
            self._emit_subagent_event(
                context,
                "agent_thread_failed",
                subagent_id=child_id,
                parent_id=parent_id,
                mode="thread",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                thread_id=child_id,
                status="failed",
                error=str(exc),
            )
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "tool": "spawn_agent_thread",
                    "mode": "agent_thread",
                    "thread_id": child_id,
                    "agent_id": child_id,
                    "template_name": template_name,
                    "status": "failed",
                    "lineage": list(lineage),
                    "error": str(exc),
                },
                state_updates={"subagent_state": failed_state},
            )
        completed_record = AgentThreadRecord(
            thread_id=child_id,
            agent_id=child_id,
            parent_agent_id=parent_id,
            target=target,
            template_name=template_name,
            mode="thread",
            status=result.status,
            session_id=session_id,
            memory_namespace=scoped_memory_namespace,
            lineage=tuple(lineage),
            created_iteration=int(context.iteration),
            last_activity_iteration=int(context.iteration),
            context_mode=context_mode,
            instructions=instructions,
            expected_output=expected_output,
        )
        threaded_state = self.communication_runtime.upsert_thread(threaded_state, completed_record)
        threaded_state = self._merge_result_subagent_state(threaded_state, result)
        if result.clarification_request is not None:
            threaded_state = threaded_state.merged(
                {
                    "blocked_clarifications": [
                        {
                            "subagent_id": child_id,
                            "mode": "thread",
                            "thread_id": child_id,
                            "lineage": lineage,
                            "request": copy.deepcopy(result.clarification_request),
                        }
                    ]
                }
            )
            self._emit_subagent_event(
                context,
                "agent_thread_clarification_requested",
                subagent_id=child_id,
                parent_id=parent_id,
                mode="thread",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                thread_id=child_id,
                request_id=result.clarification_request.get("request_id"),
                clarification_request=copy.deepcopy(result.clarification_request),
            )
        self._emit_subagent_event(
            context,
            "agent_thread_completed",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="thread",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            thread_id=child_id,
            status=result.status,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "agent_thread",
                "thread_id": child_id,
                "agent_id": result.agent_name,
                "template_name": template_name,
                "status": result.status,
                "summary": result.summary,
                "output": result.output,
                "lineage": list(lineage),
                "background": background,
                "clarification_request": copy.deepcopy(result.clarification_request),
            },
            state_updates={"subagent_state": threaded_state},
        )

    def _send_agent_message(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        recipient = str(args.get("recipient") or "").strip()
        content = str(args.get("content") or "").strip()
        kind = str(args.get("kind") or "followup").strip() or "followup"
        explicit_thread_id = str(args.get("thread_id") or "").strip()
        correlation_id = str(args.get("correlation_id") or "").strip() or None
        requires_ack = bool(args.get("requires_ack", False))
        if not recipient:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={"tool": "send_agent_message", "error": "send_agent_message requires recipient"},
            )
        if not content:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={"tool": "send_agent_message", "error": "send_agent_message requires content"},
            )

        state = self._ensure_state(context)
        runtime = self.communication_runtime
        try:
            record = runtime.require_thread(state, recipient, explicit_thread_id)
        except ValueError as exc:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "tool": "send_agent_message",
                    "mode": "agent_message",
                    "status": "failed",
                    "error": str(exc),
                },
            )

        parent_id = state.active_agent_id or self.parent_agent.name
        lineage = list(record.lineage or [parent_id, record.agent_id])
        try:
            message = runtime.build_message(
                sender_agent_id=parent_id,
                recipient_agent_id=record.agent_id,
                thread_id=record.thread_id,
                kind=kind,
                content=content,
                iteration=int(context.iteration),
                correlation_id=correlation_id,
                requires_ack=requires_ack,
            )
            messaged_state = runtime.append_message(state, message)
        except ValueError as exc:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "tool": "send_agent_message",
                    "mode": "agent_message",
                    "status": "failed",
                    "thread_id": record.thread_id,
                    "error": str(exc),
                },
            )

        template = self._resolve_template(record.target, mode="delegate")
        child, memory_policy, template_name = self._build_subagent(
            template=template,
            child_id=record.agent_id,
            lineage=lineage,
            mode="delegate",
            target=record.target,
            task=content,
            instructions=record.instructions,
            expected_output=record.expected_output,
        )
        child_run_id = self._build_child_run_id(
            session_id=record.session_id,
            child_id=record.agent_id,
        )
        self._emit_subagent_event(
            context,
            "agent_message_sent",
            subagent_id=record.agent_id,
            parent_id=record.parent_agent_id or parent_id,
            mode="message",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            thread_id=record.thread_id,
            message_id=message.message_id,
            kind=message.kind,
        )
        event = getattr(context, "event", {}) or {}
        try:
            result = self._run_child(
                agent=child,
                mode="message",
                child_id=record.agent_id,
                lineage=lineage,
                template_name=template_name,
                session_id=record.session_id,
                memory_namespace=record.memory_namespace if memory_policy == "scoped_persistent" else "",
                input_messages=content,
                max_iterations=int(event.get("max_iterations") or 6),
                child_run_id=child_run_id,
                callback=getattr(context, "callback", None),
                on_tool_confirm=event.get("on_tool_confirm"),
                on_human_input=event.get("on_human_input"),
                on_max_iterations=event.get("on_max_iterations"),
                execution_guard=getattr(context, "execution_guard", None),
            )
        except Exception as exc:
            failed_record = AgentThreadRecord(
                thread_id=record.thread_id,
                agent_id=record.agent_id,
                parent_agent_id=record.parent_agent_id,
                target=record.target,
                template_name=record.template_name,
                mode=record.mode,
                status="failed",
                session_id=record.session_id,
                memory_namespace=record.memory_namespace,
                lineage=record.lineage,
                created_iteration=record.created_iteration,
                last_activity_iteration=int(context.iteration),
                context_mode=record.context_mode,
                instructions=record.instructions,
                expected_output=record.expected_output,
                close_reason=str(exc),
            )
            failed_state = runtime.upsert_thread(messaged_state, failed_record)
            failed_state = self._merge_child_exception_subagent_state(failed_state, exc)
            self._emit_subagent_event(
                context,
                "agent_message_failed",
                subagent_id=record.agent_id,
                parent_id=record.parent_agent_id or parent_id,
                mode="message",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                thread_id=record.thread_id,
                message_id=message.message_id,
                status="failed",
                error=str(exc),
            )
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "tool": "send_agent_message",
                    "mode": "agent_message",
                    "status": "failed",
                    "thread_id": record.thread_id,
                    "message": message.to_dict(),
                    "error": str(exc),
                },
                state_updates={"subagent_state": failed_state},
            )

        completed_record = AgentThreadRecord(
            thread_id=record.thread_id,
            agent_id=record.agent_id,
            parent_agent_id=record.parent_agent_id,
            target=record.target,
            template_name=record.template_name,
            mode=record.mode,
            status=result.status,
            session_id=record.session_id,
            memory_namespace=record.memory_namespace,
            lineage=record.lineage,
            created_iteration=record.created_iteration,
            last_activity_iteration=int(context.iteration),
            context_mode=record.context_mode,
            instructions=record.instructions,
            expected_output=record.expected_output,
        )
        updated_state = runtime.upsert_thread(messaged_state, completed_record)
        updated_state = self._merge_result_subagent_state(updated_state, result)
        if result.clarification_request is not None:
            updated_state = updated_state.merged(
                {
                    "blocked_clarifications": [
                        {
                            "subagent_id": record.agent_id,
                            "mode": "message",
                            "thread_id": record.thread_id,
                            "lineage": lineage,
                            "request": copy.deepcopy(result.clarification_request),
                        }
                    ]
                }
            )
            self._emit_subagent_event(
                context,
                "agent_message_clarification_requested",
                subagent_id=record.agent_id,
                parent_id=record.parent_agent_id or parent_id,
                mode="message",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                thread_id=record.thread_id,
                request_id=result.clarification_request.get("request_id"),
                clarification_request=copy.deepcopy(result.clarification_request),
            )
        self._emit_subagent_event(
            context,
            "agent_message_completed",
            subagent_id=record.agent_id,
            parent_id=record.parent_agent_id or parent_id,
            mode="message",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            thread_id=record.thread_id,
            message_id=message.message_id,
            status=result.status,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "agent_message",
                "status": result.status,
                "thread_id": record.thread_id,
                "message": message.to_dict(),
                "reply": result.to_dict(),
                "clarification_request": copy.deepcopy(result.clarification_request),
            },
            state_updates={"subagent_state": updated_state},
        )

    def _wait_agent_messages(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        thread_ids = args.get("thread_ids")
        condition = str(args.get("condition") or "all_done").strip() or "all_done"
        if not isinstance(thread_ids, list) or not thread_ids:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "wait_agent_messages requires thread_ids"})
        if condition not in {"all_done", "any_done", "idle"}:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={"error": "wait_agent_messages condition must be one of all_done, any_done, or idle"},
            )

        state = self._ensure_state(context)
        threads: list[dict[str, Any]] = []
        found_threads: list[dict[str, Any]] = []
        has_unknown = False
        for thread_id in thread_ids:
            normalized_thread_id = str(thread_id or "").strip()
            raw = state.threads.get(normalized_thread_id)
            if isinstance(raw, dict):
                thread = copy.deepcopy(raw)
                threads.append(thread)
                found_threads.append(thread)
            else:
                has_unknown = True
                threads.append({"thread_id": normalized_thread_id, "status": "not_found"})

        if has_unknown:
            status = "not_found"
        elif condition == "all_done":
            status = "completed" if all(thread.get("status") in _TERMINAL_THREAD_STATUSES for thread in threads) else "running"
        elif condition == "any_done":
            status = "completed" if any(thread.get("status") in _TERMINAL_THREAD_STATUSES for thread in threads) else "running"
        else:
            idle_statuses = {"idle", *_TERMINAL_THREAD_STATUSES}
            status = "completed" if all(thread.get("status") in idle_statuses for thread in found_threads) else "running"
        emit_loop_event(
            context.loop,
            context.callback,
            "agent_message_wait_completed",
            context.run_id,
            iteration=context.iteration,
            root_agent=state.root_agent_id or self.parent_agent.name,
            root_run_id=context.run_id,
            status=status,
            thread_ids=[str(thread_id or "").strip() for thread_id in thread_ids],
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={"mode": "agent_wait", "status": status, "threads": threads},
        )

    def _close_agent_thread(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        thread_id = str(args.get("thread_id") or "").strip()
        reason = str(args.get("reason") or "").strip()
        if not thread_id:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "close_agent_thread requires thread_id"})

        state = self._ensure_state(context)
        try:
            closed_state = self.communication_runtime.close_thread(state, thread_id, reason=reason)
        except ValueError as exc:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": str(exc)})
        thread = copy.deepcopy(closed_state.threads[thread_id])
        lineage = list(thread.get("lineage") or [])
        parent_id = str(thread.get("parent_agent_id") or state.active_agent_id or self.parent_agent.name)
        template_name = thread.get("template_name")
        self._emit_subagent_event(
            context,
            "agent_thread_closed",
            subagent_id=thread_id,
            parent_id=parent_id,
            mode=str(thread.get("mode") or "thread"),
            template=str(template_name) if template_name is not None else None,
            lineage=lineage,
            thread_id=thread_id,
            reason=reason,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={"mode": "agent_thread_close", "status": "closed", "thread": thread},
            state_updates={"subagent_state": closed_state},
        )

    def _write_agent_board(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        raw_kind = args.get("kind")
        raw_title = args.get("title")
        raw_content = args.get("content")
        if not isinstance(raw_kind, str) or not raw_kind.strip():
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={"tool": "write_agent_board", "error": "write_agent_board requires kind"},
            )
        if not isinstance(raw_title, str) or not raw_title.strip():
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={"tool": "write_agent_board", "error": "write_agent_board requires title"},
            )
        if not isinstance(raw_content, str) or not raw_content.strip():
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={"tool": "write_agent_board", "error": "write_agent_board requires content"},
            )
        kind = raw_kind.strip()
        title = raw_title.strip()
        content = raw_content.strip()

        board_id = str(args.get("board_id") or "default").strip() or "default"
        confidence_arg = args.get("confidence")
        confidence: float | None = None
        if "confidence" in args:
            if isinstance(confidence_arg, bool) or not isinstance(confidence_arg, (int, float)):
                return ToolRuntimeOutcome(
                    handled=True,
                    tool_result={
                        "tool": "write_agent_board",
                        "mode": "agent_board_write",
                        "status": "failed",
                        "error": "write_agent_board confidence must be between 0 and 1",
                    },
                )
            confidence = float(confidence_arg)
            if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
                return ToolRuntimeOutcome(
                    handled=True,
                    tool_result={
                        "tool": "write_agent_board",
                        "mode": "agent_board_write",
                        "status": "failed",
                        "error": "write_agent_board confidence must be between 0 and 1",
                    },
                )
        state = self._ensure_state(context)
        author_agent_id = state.active_agent_id or self.parent_agent.name
        runtime = self.communication_runtime
        tags: tuple[str, ...] = ()
        if "tags" in args:
            raw_tags = args.get("tags")
            if not isinstance(raw_tags, list) or any(not isinstance(item, str) for item in raw_tags):
                return ToolRuntimeOutcome(
                    handled=True,
                    tool_result={
                        "tool": "write_agent_board",
                        "mode": "agent_board_write",
                        "status": "failed",
                        "error": "write_agent_board tags must be an array of strings",
                    },
                )
            tags = tuple(raw_tags)
        refs: tuple[str, ...] = ()
        if "refs" in args:
            raw_refs = args.get("refs")
            if not isinstance(raw_refs, list) or any(not isinstance(item, str) for item in raw_refs):
                return ToolRuntimeOutcome(
                    handled=True,
                    tool_result={
                        "tool": "write_agent_board",
                        "mode": "agent_board_write",
                        "status": "failed",
                        "error": "write_agent_board refs must be an array of strings",
                    },
                )
            refs = tuple(raw_refs)
        supersedes_item_id = str(args.get("supersedes_item_id") or "").strip() or None
        try:
            item = runtime.build_board_item(
                board_id=board_id,
                author_agent_id=author_agent_id,
                kind=kind,
                title=title,
                content=content,
                tags=tags,
                confidence=confidence,
                refs=refs,
                iteration=int(context.iteration),
                supersedes_item_id=supersedes_item_id,
            )
            updated_state = runtime.append_board_item(state, item)
        except ValueError as exc:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "tool": "write_agent_board",
                    "mode": "agent_board_write",
                    "status": "failed",
                    "error": str(exc),
                },
            )

        emit_loop_event(
            context.loop,
            context.callback,
            "agent_board_item_written",
            context.run_id,
            iteration=context.iteration,
            root_agent=state.root_agent_id or self.parent_agent.name,
            root_run_id=context.run_id,
            board_id=item.board_id,
            item_id=item.item_id,
            kind=item.kind,
            author_agent_id=item.author_agent_id,
            title=item.title,
            tags=list(item.tags),
            supersedes_item_id=item.supersedes_item_id,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "agent_board_write",
                "status": "written",
                "item": item.to_dict(),
            },
            state_updates={"subagent_state": updated_state},
        )

    def _read_agent_board(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        board_id = str(args.get("board_id") or "default").strip() or "default"
        kinds: tuple[str, ...] = ()
        if "kinds" in args:
            raw_kinds = args.get("kinds")
            if not isinstance(raw_kinds, list) or any(not isinstance(item, str) for item in raw_kinds):
                return ToolRuntimeOutcome(
                    handled=True,
                    tool_result={
                        "tool": "read_agent_board",
                        "mode": "agent_board_read",
                        "status": "failed",
                        "error": "read_agent_board kinds must be an array of strings",
                    },
                )
            kinds = tuple(raw_kinds)
        if "kind" in args:
            raw_kind = args.get("kind")
            if not isinstance(raw_kind, str) or not raw_kind.strip():
                return ToolRuntimeOutcome(
                    handled=True,
                    tool_result={
                        "tool": "read_agent_board",
                        "mode": "agent_board_read",
                        "status": "failed",
                        "error": "read_agent_board kind must be a non-empty string",
                    },
                )
            kind = raw_kind.strip()
            if kind not in kinds:
                kinds = (*kinds, kind)
        tags: tuple[str, ...] = ()
        if "tags" in args:
            raw_tags = args.get("tags")
            if not isinstance(raw_tags, list) or any(not isinstance(item, str) for item in raw_tags):
                return ToolRuntimeOutcome(
                    handled=True,
                    tool_result={
                        "tool": "read_agent_board",
                        "mode": "agent_board_read",
                        "status": "failed",
                        "error": "read_agent_board tags must be an array of strings",
                    },
                )
            tags = tuple(raw_tags)
        author_agent_id = str(args.get("author_agent_id") or "").strip()
        limit_arg = args.get("limit", 50)
        if "limit" in args and (isinstance(limit_arg, bool) or not isinstance(limit_arg, int) or limit_arg <= 0):
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "tool": "read_agent_board",
                    "mode": "agent_board_read",
                    "status": "failed",
                    "error": "read_agent_board limit must be a positive integer",
                },
            )
        limit = int(limit_arg)
        state = self._ensure_state(context)
        items = self.communication_runtime.read_board_items(
            state,
            board_id=board_id,
            kinds=kinds,
            tags=tags,
            author_agent_id=author_agent_id,
            limit=limit,
        )
        emit_loop_event(
            context.loop,
            context.callback,
            "agent_board_read",
            context.run_id,
            iteration=context.iteration,
            root_agent=state.root_agent_id or self.parent_agent.name,
            root_run_id=context.run_id,
            board_id=board_id,
            count=len(items),
            kinds=list(kinds),
            tags=list(tags),
            author_agent_id=author_agent_id,
            limit=limit,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "agent_board_read",
                "status": "ok",
                "items": items,
                "count": len(items),
            },
        )

    def _return_to_parent(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        del context
        args = _parse_arguments(tool_call.arguments)
        summary = str(args.get("summary") or "").strip()
        result = str(args.get("result") or "").strip()
        status = str(args.get("status") or "completed").strip() or "completed"
        if not summary:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "return_to_parent requires summary"})
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "return_to_parent",
                "status": status,
                "summary": summary,
                "result": result,
            },
        )

    def _return_handoff_to_subagent(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        if not self.policy.allow_return_handoff:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "return handoff is disabled by policy"})

        args = _parse_arguments(tool_call.arguments)
        target = str(args.get("target") or "").strip()
        reason = str(args.get("reason") or "").strip()
        expected_return = str(args.get("expected_return") or "").strip()
        carry_context = bool(args.get("carry_context", True))
        if not target:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "return_handoff_to_subagent requires target"})

        state = self._ensure_state(context)
        parent_id = state.active_agent_id or self.parent_agent.name
        child_id, lineage, next_state = self._next_subagent_identity(state=state, target=target, mode="handoff")
        template = self._resolve_template(target, mode="handoff")
        if template is None and self.policy.handoff_requires_template:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={"error": "return_handoff_to_subagent requires a registered template"},
            )
        child, memory_policy, template_name = self._build_subagent(
            template=template,
            child_id=child_id,
            lineage=lineage,
            mode="handoff",
            target=target,
            task=reason or "Temporarily take over this segment and return to the parent.",
            instructions="Return control to the parent when your segment is complete.",
            expected_output=expected_return or "Return a concise summary and result to the parent.",
        )
        session_id = f"{context.session_id or context.run_id}:{child_id}"
        memory_namespace = f"{context.memory_namespace or context.session_id or context.run_id}:{child_id}"
        child_run_id = self._build_child_run_id(
            session_id=context.session_id or context.run_id,
            child_id=child_id,
        )
        frame = {
            "frame_id": child_run_id,
            "parent_agent_id": parent_id,
            "child_agent_id": child_id,
            "thread_id": child_id,
            "target": target,
            "template_name": template_name,
            "lineage": list(lineage),
        }
        running_state = next_state.merged({"return_handoff_stack": [frame]})
        self._emit_subagent_event(
            context,
            "subagent_return_handoff_started",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="return_handoff",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
        )

        sanitized_messages = _sanitize_handoff_messages(context.latest_messages(), tool_call=tool_call)
        input_messages: str | list[dict[str, Any]]
        if carry_context:
            input_messages = sanitized_messages
        else:
            input_messages = reason or "Continue this segment."

        try:
            child_result = self._run_child(
                agent=child,
                mode="return_handoff",
                child_id=child_id,
                lineage=lineage,
                template_name=template_name,
                session_id=session_id,
                memory_namespace=memory_namespace if memory_policy == "scoped_persistent" else "",
                input_messages=input_messages,
                max_iterations=int(context.event.get("max_iterations") or 6),
                child_run_id=child_run_id,
                callback=context.callback,
                on_tool_confirm=context.event.get("on_tool_confirm"),
                on_human_input=context.event.get("on_human_input"),
                on_max_iterations=context.event.get("on_max_iterations"),
                execution_guard=getattr(context, "execution_guard", None),
            )
        except Exception as exc:
            failed_state = self._merge_child_exception_subagent_state(running_state, exc)
            failed_state.return_handoff_stack = [
                item
                for item in failed_state.return_handoff_stack
                if not (isinstance(item, dict) and item.get("frame_id") == child_run_id)
            ]
            self._emit_subagent_event(
                context,
                "subagent_return_handoff_completed",
                subagent_id=child_id,
                parent_id=parent_id,
                mode="return_handoff",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                status="failed",
                error=str(exc),
            )
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "mode": "return_handoff",
                    "status": "failed",
                    "agent_name": child.name,
                    "template_name": template_name,
                    "lineage": list(lineage),
                    "return": {
                        "mode": "return_to_parent",
                        "status": "failed",
                        "summary": str(exc),
                        "result": "",
                    },
                    "summary": str(exc),
                    "error": str(exc),
                },
                state_updates={"subagent_state": failed_state},
            )

        return_payload = _extract_return_to_parent_payload(child_result.messages)
        if return_payload is None:
            return_payload = {
                "mode": "return_to_parent",
                "status": child_result.status,
                "summary": child_result.summary or child_result.output,
                "result": child_result.output,
            }
        clarification_request = copy.deepcopy(child_result.clarification_request)
        if clarification_request is not None:
            return_payload.setdefault("clarification_request", copy.deepcopy(clarification_request))
        result_state = self._merge_result_subagent_state(running_state, child_result)
        if clarification_request is not None:
            result_state = result_state.merged(
                {
                    "blocked_clarifications": [
                        {
                            "subagent_id": child_id,
                            "mode": "return_handoff",
                            "lineage": lineage,
                            "request": copy.deepcopy(clarification_request),
                        }
                    ]
                }
            )
        result_state.return_handoff_stack = [
            item
            for item in result_state.return_handoff_stack
            if not (isinstance(item, dict) and item.get("frame_id") == child_run_id)
        ]
        status = str(return_payload.get("status") or child_result.status)
        summary = str(return_payload.get("summary") or child_result.summary or child_result.output)
        if clarification_request is not None:
            self._emit_subagent_event(
                context,
                "subagent_clarification_requested",
                subagent_id=child_id,
                parent_id=parent_id,
                mode="return_handoff",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                request_id=clarification_request.get("request_id"),
                clarification_request=copy.deepcopy(clarification_request),
            )
        self._emit_subagent_event(
            context,
            "subagent_return_handoff_completed",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="return_handoff",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            status=status,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "return_handoff",
                "status": status,
                "agent_name": child.name,
                "template_name": template_name,
                "lineage": list(lineage),
                "return": copy.deepcopy(return_payload),
                "summary": summary,
                "clarification_request": copy.deepcopy(clarification_request),
            },
            state_updates={"subagent_state": result_state},
        )

    def _delegate(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        target = str(args.get("target") or "").strip()
        task = str(args.get("task") or "").strip()
        instructions = str(args.get("instructions") or "").strip()
        expected_output = str(args.get("expected_output") or "").strip()
        output_mode = str(args.get("output_mode") or "summary").strip() or "summary"
        if not target:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "delegate_to_subagent requires target"})
        if not task:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "delegate_to_subagent requires task"})
        state = self._ensure_state(context)
        child_id, lineage, next_state = self._next_subagent_identity(state=state, target=target, mode="delegate")
        template = self._resolve_template(target, mode="delegate")
        child, memory_policy, template_name = self._build_subagent(
            template=template,
            child_id=child_id,
            lineage=lineage,
            mode="delegate",
            target=target,
            task=task,
            instructions=instructions,
            expected_output=expected_output,
        )
        session_id = f"{context.session_id or context.run_id}:{child_id}"
        memory_namespace = f"{context.memory_namespace or context.session_id or context.run_id}:{child_id}"
        parent_id = state.active_agent_id or self.parent_agent.name
        child_run_id = self._build_child_run_id(
            session_id=context.session_id or context.run_id,
            child_id=child_id,
        )
        self._emit_subagent_event(context, "subagent_spawned", subagent_id=child_id, parent_id=parent_id, mode="delegate", template=template_name, lineage=lineage, child_run_id=child_run_id)
        self._emit_subagent_event(context, "subagent_started", subagent_id=child_id, parent_id=parent_id, mode="delegate", template=template_name, lineage=lineage, child_run_id=child_run_id)
        try:
            result = self._run_child(
                agent=child,
                mode="delegate",
                child_id=child_id,
                lineage=lineage,
                template_name=template_name,
                session_id=session_id,
                memory_namespace=memory_namespace if memory_policy == "scoped_persistent" else "",
                input_messages=task,
                max_iterations=int(context.event.get("max_iterations") or 6),
                child_run_id=child_run_id,
                callback=context.callback,
                on_tool_confirm=context.event.get("on_tool_confirm"),
                on_human_input=context.event.get("on_human_input"),
                on_max_iterations=context.event.get("on_max_iterations"),
                execution_guard=getattr(context, "execution_guard", None),
            )
        except Exception as exc:
            failed_state = self._merge_child_exception_subagent_state(next_state, exc)
            self._emit_subagent_event(
                context,
                "subagent_failed",
                subagent_id=child_id,
                parent_id=parent_id,
                mode="delegate",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                status="failed",
                error=str(exc),
            )
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "tool": "delegate_to_subagent",
                    "mode": "delegate",
                    "agent_name": child_id,
                    "template_name": template_name,
                    "status": "failed",
                    "lineage": list(lineage),
                    "error": str(exc),
                },
                state_updates={"subagent_state": failed_state},
            )
        template_payload = self._render_result(result=result, output_mode=output_mode, template_name=template_name)
        result_state = self._merge_result_subagent_state(next_state, result)
        update = {
            "subagent_state": result_state,
        }
        if result.clarification_request is not None:
            update["subagent_state"] = result_state.merged(
                {
                    "blocked_clarifications": [
                        {
                            "subagent_id": child_id,
                            "mode": "delegate",
                            "lineage": lineage,
                            "request": copy.deepcopy(result.clarification_request),
                        }
                    ]
                }
            )
            self._emit_subagent_event(
                context,
                "subagent_clarification_requested",
                subagent_id=child_id,
                parent_id=parent_id,
                mode="delegate",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                request_id=result.clarification_request.get("request_id"),
            )
        self._emit_subagent_event(
            context,
            "subagent_completed" if result.error == "" else "subagent_failed",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="delegate",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            status=result.status,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result=template_payload,
            state_updates=update,
        )

    def _handoff(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        target = str(args.get("target") or "").strip()
        reason = str(args.get("reason") or "").strip()
        carry_context = bool(args.get("carry_context", True))
        if not target:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "handoff_to_subagent requires target"})
        state = self._ensure_state(context)
        child_id, lineage, next_state = self._next_subagent_identity(state=state, target=target, mode="handoff")
        template = self._resolve_template(target, mode="handoff")
        child, memory_policy, template_name = self._build_subagent(
            template=template,
            child_id=child_id,
            lineage=lineage,
            mode="handoff",
            target=target,
            task=reason or "Continue handling the conversation.",
            instructions="",
            expected_output="Take over the conversation and produce the final answer.",
        )
        session_id = f"{context.session_id or context.run_id}:{child_id}"
        memory_namespace = f"{context.memory_namespace or context.session_id or context.run_id}:{child_id}"
        parent_id = state.active_agent_id or self.parent_agent.name
        child_run_id = self._build_child_run_id(
            session_id=context.session_id or context.run_id,
            child_id=child_id,
        )
        self._emit_subagent_event(context, "subagent_spawned", subagent_id=child_id, parent_id=parent_id, mode="handoff", template=template_name, lineage=lineage, child_run_id=child_run_id)
        self._emit_subagent_event(context, "subagent_handoff", subagent_id=child_id, parent_id=parent_id, mode="handoff", template=template_name, lineage=lineage, reason=reason, child_run_id=child_run_id)
        self._emit_subagent_event(context, "subagent_started", subagent_id=child_id, parent_id=parent_id, mode="handoff", template=template_name, lineage=lineage, child_run_id=child_run_id)
        sanitized_messages = _sanitize_handoff_messages(
            context.latest_messages(),
            tool_call=tool_call,
        )
        if carry_context:
            input_messages: str | list[dict[str, Any]] = sanitized_messages
        else:
            input_messages = reason or "Continue the task."
        try:
            result = self._run_child(
                agent=child,
                mode="handoff",
                child_id=child_id,
                lineage=lineage,
                template_name=template_name,
                session_id=session_id,
                memory_namespace=memory_namespace if memory_policy == "scoped_persistent" else "",
                input_messages=input_messages,
                max_iterations=int(context.event.get("max_iterations") or 6),
                child_run_id=child_run_id,
                callback=context.callback,
                on_tool_confirm=context.event.get("on_tool_confirm"),
                on_human_input=context.event.get("on_human_input"),
                on_max_iterations=context.event.get("on_max_iterations"),
                execution_guard=getattr(context, "execution_guard", None),
            )
        except Exception as exc:
            failed_state = self._merge_child_exception_subagent_state(next_state, exc)
            self._emit_subagent_event(
                context,
                "subagent_failed",
                subagent_id=child_id,
                parent_id=parent_id,
                mode="handoff",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                status="failed",
                error=str(exc),
            )
            return ToolRuntimeOutcome(
                handled=True,
                tool_result={
                    "tool": "handoff_to_subagent",
                    "mode": "handoff",
                    "agent_name": child_id,
                    "template_name": template_name,
                    "status": "failed",
                    "lineage": list(lineage),
                    "error": str(exc),
                },
                state_updates={"subagent_state": failed_state},
            )
        result_state = self._merge_result_subagent_state(next_state, result)
        if result.clarification_request is not None:
            blocked_state = result_state.merged(
                {
                    "blocked_clarifications": [
                        {
                            "subagent_id": child_id,
                            "mode": "handoff",
                            "lineage": lineage,
                            "request": copy.deepcopy(result.clarification_request),
                        }
                    ]
                }
            )
            self._emit_subagent_event(
                context,
                "subagent_clarification_requested",
                subagent_id=child_id,
                parent_id=parent_id,
                mode="handoff",
                template=template_name,
                lineage=lineage,
                child_run_id=child_run_id,
                request_id=result.clarification_request.get("request_id"),
            )
            return ToolRuntimeOutcome(
                handled=True,
                tool_result=result.to_dict(),
                state_updates={"subagent_state": blocked_state},
            )
        handoff_state = result_state.merged(
            {
                "active_agent_id": child_id,
                "active_lineage": lineage,
                "handoff_stack": [
                    *next_state.handoff_stack,
                    {
                        "from_agent_id": parent_id,
                        "to_agent_id": child_id,
                        "lineage": list(lineage),
                        "template": template_name,
                    },
                ],
            }
        )
        final_text = result.output or result.summary
        final_message = {"role": "assistant", "content": final_text}
        self._emit_subagent_event(
            context,
            "subagent_completed",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="handoff",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            status=result.status,
        )
        return ToolRuntimeOutcome(
            handled=True,
            state_updates={
                "subagent_state": handoff_state,
                "transcript": [*sanitized_messages, final_message],
                "run_status": "completed",
                "pending_tool_calls": [],
                "tool_batch_state": {},
                "last_continuation": None,
                "next_model_input": None,
            },
        )

    def _worker_batch(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        raw_tasks = args.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "spawn_worker_batch requires non-empty tasks"})
        default_target = str(args.get("target") or "").strip()
        default_instructions = str(args.get("instructions") or "").strip()
        aggregate_mode = str(args.get("aggregate_mode") or "ordered_list").strip() or "ordered_list"
        state = self._ensure_state(context)
        parent_id = state.active_agent_id or self.parent_agent.name
        batch_id = str(uuid.uuid4())
        next_state = state.copy()
        next_state.running_batches[batch_id] = {
            "status": "running",
            "task_count": len(raw_tasks),
            "parent_id": parent_id,
        }
        self._emit_subagent_event(
            context,
            "subagent_batch_started",
            subagent_id=parent_id,
            parent_id=parent_id,
            mode="worker",
            template=default_target or None,
            lineage=list(state.active_lineage or [self.parent_agent.name]),
            batch_id=batch_id,
            task_count=len(raw_tasks),
        )
        prepared_items: list[dict[str, Any]] = []
        allocation_state = next_state.copy()
        for index, item in enumerate(raw_tasks):
            if not isinstance(item, dict):
                continue
            task = str(item.get("task") or "").strip()
            if not task:
                prepared_items.append(
                    {
                        "type": "prebuilt",
                        "result": SubagentResult(
                            mode="worker",
                            agent_name="",
                            template_name=None,
                            status="failed",
                            error="worker task is required",
                        ),
                    }
                )
                continue
            target = str(item.get("target") or default_target).strip()
            instructions = str(item.get("instructions") or default_instructions).strip()
            expected_output = str(item.get("expected_output") or "").strip()
            output_mode = str(item.get("output_mode") or "summary").strip() or "summary"
            child_id, lineage, allocation_state = self._next_subagent_identity(
                state=allocation_state,
                target=target or f"worker{index+1}",
                mode="worker",
            )
            template = self._resolve_template(target, mode="worker") if target else None
            if template is not None and not template.parallel_safe:
                prepared_items.append(
                    {
                        "type": "prebuilt",
                        "result": SubagentResult(
                            mode="worker",
                            agent_name=child_id,
                            template_name=template.name,
                            status="failed",
                            error=f"subagent template {template.name!r} is not parallel_safe",
                            lineage=lineage,
                        ),
                    }
                )
                continue
            child, memory_policy, template_name = self._build_subagent(
                template=template,
                child_id=child_id,
                lineage=lineage,
                mode="worker",
                target=target or f"worker{index+1}",
                task=task,
                instructions=instructions,
                expected_output=expected_output,
            )
            session_id = f"{context.session_id or context.run_id}:{child_id}"
            memory_namespace = f"{context.memory_namespace or context.session_id or context.run_id}:{child_id}"
            worker_run_id = self._build_child_run_id(
                session_id=context.session_id or context.run_id,
                child_id=child_id,
            )
            prepared_items.append(
                {
                    "type": "run",
                    "index": index,
                    "task": task,
                    "child_id": child_id,
                    "child_run_id": worker_run_id,
                    "lineage": lineage,
                    "template_name": template_name,
                    "output_mode": output_mode,
                    "agent": child,
                    "session_id": session_id,
                    "memory_namespace": memory_namespace if memory_policy == "scoped_persistent" else "",
                }
            )

        def _run_item(index: int, item: dict[str, Any]) -> SubagentResult:
            if item.get("type") == "prebuilt":
                return copy.deepcopy(item["result"])
            task = str(item.get("task") or "").strip()
            child_id = str(item["child_id"])
            child_run_id = str(item.get("child_run_id") or "")
            lineage = list(item["lineage"])
            template_name = item.get("template_name")
            output_mode = str(item.get("output_mode") or "summary")
            self._emit_subagent_event(context, "subagent_spawned", subagent_id=child_id, parent_id=parent_id, mode="worker", template=template_name, lineage=lineage, batch_id=batch_id, child_run_id=child_run_id)
            self._emit_subagent_event(context, "subagent_started", subagent_id=child_id, parent_id=parent_id, mode="worker", template=template_name, lineage=lineage, batch_id=batch_id, child_run_id=child_run_id)
            try:
                result = self._run_child(
                    agent=item["agent"],
                    mode="worker",
                    child_id=child_id,
                    lineage=lineage,
                    template_name=template_name,
                    session_id=str(item["session_id"]),
                    memory_namespace=str(item["memory_namespace"]),
                    child_run_id=child_run_id,
                    input_messages=task,
                    max_iterations=int(context.event.get("max_iterations") or 6),
                    callback=context.callback,
                    on_tool_confirm=context.event.get("on_tool_confirm"),
                    on_human_input=context.event.get("on_human_input"),
                    on_max_iterations=context.event.get("on_max_iterations"),
                    execution_guard=getattr(context, "execution_guard", None),
                )
                rendered = self._render_result(result=result, output_mode=output_mode, template_name=template_name)
                result = SubagentResult(**rendered)
            except Exception as exc:
                result = SubagentResult(
                    mode="worker",
                    agent_name=child_id,
                    template_name=template_name,
                    status="failed",
                    error=str(exc),
                    lineage=lineage,
                    subagent_state=self._subagent_state_from_child_exception(exc),
                )
            event_type = "subagent_completed" if not result.error else "subagent_failed"
            self._emit_subagent_event(
                context,
                event_type,
                subagent_id=child_id,
                parent_id=parent_id,
                mode="worker",
                template=template_name,
                lineage=lineage,
                batch_id=batch_id,
                child_run_id=child_run_id,
                status=result.status,
            )
            if result.clarification_request is not None:
                self._emit_subagent_event(
                    context,
                    "subagent_clarification_requested",
                    subagent_id=child_id,
                    parent_id=parent_id,
                    mode="worker",
                    template=template_name,
                    lineage=lineage,
                    batch_id=batch_id,
                    child_run_id=child_run_id,
                    request_id=result.clarification_request.get("request_id"),
                )
            return result

        results = self.executor.execute_batch(items=prepared_items, run_item=_run_item)
        final_state = allocation_state.copy()
        final_state.running_batches.pop(batch_id, None)
        for result in results:
            final_state = self._merge_result_subagent_state(final_state, result)
        clarifications = [
            {
                "subagent_id": result.agent_name,
                "mode": "worker",
                "lineage": list(result.lineage),
                "request": copy.deepcopy(result.clarification_request),
            }
            for result in results
            if result.clarification_request is not None
        ]
        if clarifications:
            final_state.blocked_clarifications.extend(clarifications)
        self._emit_subagent_event(
            context,
            "subagent_batch_joined",
            subagent_id=parent_id,
            parent_id=parent_id,
            mode="worker",
            template=default_target or None,
            lineage=list(state.active_lineage or [self.parent_agent.name]),
            batch_id=batch_id,
            completed_count=sum(1 for result in results if result.status == "completed"),
        )
        summary_parts = [result.summary or result.output for result in results if (result.summary or result.output)]
        tool_result = {
            "mode": "worker_batch",
            "status": "completed" if all(result.status == "completed" for result in results) else "partial_failure",
            "aggregate_mode": aggregate_mode,
            "summary": "\n".join(summary_parts),
            "results": [result.to_dict() for result in results],
        }
        return ToolRuntimeOutcome(
            handled=True,
            tool_result=tool_result,
            state_updates={"subagent_state": final_state},
        )
