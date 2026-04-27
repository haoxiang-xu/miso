from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..tools.common import emit_loop_event
from ..tools.runtime import ToolRuntimeOutcome, ToolRuntimePlugin
from ..kernel.types import ToolCall
from .executor import SubagentExecutor
from .types import SubagentPolicy, SubagentResult, SubagentState, SubagentTemplate

if TYPE_CHECKING:
    from ..agent.agent import Agent as KernelAgent


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

    def can_handle(self, *, tool_call: ToolCall, context) -> bool:
        if tool_call.name not in {"delegate_to_subagent", "handoff_to_subagent", "spawn_worker_batch"}:
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
    ) -> SubagentResult:
        if not child_run_id:
            child_run_id = f"{session_id}:{child_id}:{uuid.uuid4()}"
        child_callback = callback
        if callable(callback):
            def _child_callback(event: dict[str, Any]) -> None:
                if isinstance(event, dict) and event.get("type") == "human_input_requested":
                    return None
                callback(event)

            child_callback = _child_callback
        result = agent.run(
            input_messages,
            session_id=session_id,
            memory_namespace=memory_namespace,
            max_iterations=max_iterations,
            callback=child_callback,
            on_tool_confirm=on_tool_confirm,
            on_human_input=on_human_input,
            on_max_iterations=on_max_iterations,
            run_id=child_run_id,
        )
        output = _last_assistant_text(result.messages)
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
        child_run_id = f"{session_id}:{child_id}:{uuid.uuid4()}"
        self._emit_subagent_event(context, "subagent_spawned", subagent_id=child_id, parent_id=parent_id, mode="delegate", template=template_name, lineage=lineage, child_run_id=child_run_id)
        self._emit_subagent_event(context, "subagent_started", subagent_id=child_id, parent_id=parent_id, mode="delegate", template=template_name, lineage=lineage, child_run_id=child_run_id)
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
        )
        template_payload = self._render_result(result=result, output_mode=output_mode, template_name=template_name)
        update = {
            "subagent_state": next_state,
        }
        if result.clarification_request is not None:
            update["subagent_state"] = next_state.merged(
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
        child_run_id = f"{session_id}:{child_id}:{uuid.uuid4()}"
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
        )
        if result.clarification_request is not None:
            blocked_state = next_state.merged(
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
        handoff_state = next_state.merged(
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
            worker_run_id = f"{session_id}:{child_id}:{uuid.uuid4()}"
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
            )
            rendered = self._render_result(result=result, output_mode=output_mode, template_name=template_name)
            result = SubagentResult(**rendered)
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
