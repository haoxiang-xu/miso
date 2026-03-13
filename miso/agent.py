from __future__ import annotations

import copy
import inspect
from typing import TYPE_CHECKING, Any, Callable

from ._agent_shared import as_text, normalize_mentions
from .broth import broth as Broth
from .memory import LongTermMemoryConfig, MemoryConfig, MemoryManager
from .response_format import response_format
from .tool import tool as Tool
from .tool import toolkit as Toolkit

_FORWARDED_BROTH_KEYS = {"provider", "model", "api_key", "memory_manager"}

if TYPE_CHECKING:
    from .team import Team


def _deepcopy_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _make_step_response_format(agent_name: str) -> response_format:
    return response_format(
        name=f"{agent_name}_step_result",
        schema={
            "type": "object",
            "properties": {
                "publish": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "channel": {"type": "string"},
                            "content": {"type": "string"},
                            "mentions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "kind": {"type": "string"},
                        },
                        "required": ["channel", "content"],
                        "additionalProperties": False,
                    },
                },
                "handoff_to": {"type": "string"},
                "handoff_message": {"type": "string"},
                "final": {"type": "string"},
                "idle": {"type": "boolean"},
                "artifacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["name", "content"],
                        "additionalProperties": True,
                    },
                },
            },
            "required": ["publish", "handoff_to", "handoff_message", "final", "idle", "artifacts"],
            "additionalProperties": False,
        },
    )


class Agent:
    def __init__(
        self,
        *,
        name: str,
        instructions: str = "",
        provider: str = "openai",
        model: str = "gpt-5",
        api_key: str | None = None,
        tools: list[Tool | Toolkit | Callable[..., Any]] | None = None,
        short_term_memory: MemoryManager | MemoryConfig | dict[str, Any] | None = None,
        long_term_memory: LongTermMemoryConfig | dict[str, Any] | None = None,
        defaults: dict[str, Any] | None = None,
        broth_options: dict[str, Any] | None = None,
    ):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Agent name is required")

        self.name = name.strip()
        self.instructions = instructions or ""
        self.provider = provider or "openai"
        self.model = model or "gpt-5"
        self.api_key = api_key
        self.tools = list(tools or [])
        self.defaults = _deepcopy_dict(defaults)
        self.broth_options = _deepcopy_dict(broth_options)
        self.short_term_memory = short_term_memory
        self.long_term_memory = long_term_memory
        self.memory_manager = self._coerce_memory_manager(
            short_term_memory=short_term_memory,
            long_term_memory=long_term_memory,
        )

    def _coerce_memory_manager(
        self,
        *,
        short_term_memory: MemoryManager | MemoryConfig | dict[str, Any] | None,
        long_term_memory: LongTermMemoryConfig | dict[str, Any] | None,
    ) -> MemoryManager | None:
        if isinstance(short_term_memory, MemoryManager):
            if long_term_memory is not None:
                raise ValueError("long_term_memory cannot be provided when short_term_memory is a MemoryManager")
            return short_term_memory

        config: MemoryConfig | None = None
        if short_term_memory is None and long_term_memory is None:
            return None

        if short_term_memory is None:
            config = MemoryConfig()
        elif isinstance(short_term_memory, MemoryConfig):
            config = copy.deepcopy(short_term_memory)
        elif isinstance(short_term_memory, dict):
            config = MemoryConfig(**copy.deepcopy(short_term_memory))
        else:
            raise TypeError("short_term_memory must be a MemoryManager, MemoryConfig, dict, or None")

        if long_term_memory is not None:
            if isinstance(long_term_memory, LongTermMemoryConfig):
                config.long_term = copy.deepcopy(long_term_memory)
            elif isinstance(long_term_memory, dict):
                config.long_term = LongTermMemoryConfig(**copy.deepcopy(long_term_memory))
            else:
                raise TypeError("long_term_memory must be a LongTermMemoryConfig, dict, or None")

        return MemoryManager(config=config)

    def _build_agent_toolkit(self) -> Toolkit:
        merged = Toolkit()
        for item in self.tools:
            if isinstance(item, Toolkit):
                for tool_obj in item.tools.values():
                    merged.register(tool_obj)
                continue
            if isinstance(item, Tool):
                merged.register(item)
                continue
            if callable(item):
                merged.register(item)
                continue
            raise TypeError(f"unsupported tool entry for Agent '{self.name}': {type(item).__name__}")
        return merged

    def _build_engine(self) -> Broth:
        core_kwargs: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "memory_manager": self.memory_manager,
        }

        try:
            signature = inspect.signature(Broth)
            supported_init_keys = set(signature.parameters)
        except (TypeError, ValueError):
            supported_init_keys = set(core_kwargs)

        init_kwargs = {
            key: value
            for key, value in core_kwargs.items()
            if key in supported_init_keys
        }

        for key, value in self.broth_options.items():
            if key in _FORWARDED_BROTH_KEYS:
                continue
            if key in supported_init_keys:
                init_kwargs[key] = copy.deepcopy(value)

        engine = Broth(**init_kwargs)

        for key, value in self.broth_options.items():
            if key in _FORWARDED_BROTH_KEYS or key in init_kwargs:
                continue
            if hasattr(engine, key):
                setattr(engine, key, copy.deepcopy(value))

        engine.toolkit = self._build_agent_toolkit()

        default_confirm = self.defaults.get("on_tool_confirm")
        if callable(default_confirm):
            engine.on_tool_confirm = default_confirm
        return engine

    def _normalize_messages(self, messages: str | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if messages is None:
            return []
        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]
        if isinstance(messages, list):
            return copy.deepcopy([item for item in messages if isinstance(item, dict)])
        raise TypeError("messages must be a string, a list of messages, or None")

    def _compose_messages(
        self,
        messages: str | list[dict[str, Any]] | None,
        *,
        extra_system_messages: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        normalized = self._normalize_messages(messages)
        composed: list[dict[str, Any]] = []
        if self.instructions.strip():
            composed.append({"role": "system", "content": self.instructions.strip()})
        for item in extra_system_messages or []:
            if isinstance(item, str) and item.strip():
                composed.append({"role": "system", "content": item.strip()})
        composed.extend(normalized)
        return composed

    def _merge_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        merged = _deepcopy_dict(self.defaults.get("payload"))
        if payload:
            merged.update(copy.deepcopy(payload))
        return merged

    def _extract_last_assistant_text(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "assistant":
                return as_text(message.get("content")).strip()
        return ""

    def run(
        self,
        messages: str | list[dict[str, Any]] | None,
        *,
        payload: dict[str, Any] | None = None,
        response_format: response_format | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        max_iterations: int | None = None,
        previous_response_id: str | None = None,
        on_tool_confirm: Callable | None = None,
        on_continuation_request: Callable | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        engine = self._build_engine()
        return engine.run(
            messages=self._compose_messages(messages),
            payload=self._merge_payload(payload),
            response_format=response_format or self.defaults.get("response_format"),
            callback=callback,
            verbose=verbose,
            max_iterations=max_iterations,
            previous_response_id=previous_response_id,
            on_tool_confirm=on_tool_confirm or self.defaults.get("on_tool_confirm"),
            on_continuation_request=on_continuation_request,
            session_id=session_id,
            memory_namespace=memory_namespace,
        )

    def step(
        self,
        *,
        inbox: list[dict[str, Any]],
        channels: dict[str, list[str]],
        owner: str,
        team_transcript: list[dict[str, Any]] | None = None,
        mode: str = "channel_collab",
        payload: dict[str, Any] | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        max_iterations: int | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
    ) -> dict[str, Any]:
        inbox = copy.deepcopy([item for item in inbox if isinstance(item, dict)])
        transcript = copy.deepcopy([item for item in (team_transcript or []) if isinstance(item, dict)])

        channel_list = ", ".join(sorted(channels))
        inbox_text = self._format_envelopes(inbox) or "[no inbox items]"
        transcript_text = self._format_envelopes(transcript[-24:]) or "[no transcript yet]"

        coordination_prompt = f"""
You are agent "{self.name}" inside a multi-agent team.
Mode: {mode}
Owner agent: {owner}
Available channels: {channel_list}

You are executing exactly one scheduling step.
Read the inbox and transcript, then decide one of:
1. Publish one or more channel messages.
2. Hand off to another agent.
3. Produce the final user-facing answer.
4. Stay idle.

Rules:
- Publish only to the listed channels.
- Mentions are optional hints, not forced dispatch.
- Prefer concise coordination messages.
- Only set "final" when you want the team to stop with a user-facing answer.
- If you have nothing useful to do now, set "idle" to true.
""".strip()

        user_prompt = f"""
[Inbox]
{inbox_text}

[Recent Transcript]
{transcript_text}
""".strip()

        fmt = _make_step_response_format(self.name)
        conversation, bundle = self._run_step_with_prompt(
            coordination_prompt=coordination_prompt,
            user_prompt=user_prompt,
            payload=payload,
            callback=callback,
            verbose=verbose,
            max_iterations=max_iterations,
            session_id=session_id,
            memory_namespace=memory_namespace,
            response_format=fmt,
        )

        parsed = fmt.parse(self._extract_last_assistant_text(conversation))
        publish: list[dict[str, Any]] = []
        for item in parsed.get("publish", []):
            if not isinstance(item, dict):
                continue
            channel = str(item.get("channel", "")).strip()
            content = str(item.get("content", "")).strip()
            kind = str(item.get("kind", "")).strip() or "message"
            if not channel or not content:
                continue
            publish.append({
                "channel": channel,
                "content": content,
                "kind": kind,
                "mentions": normalize_mentions(item.get("mentions"), content=content),
            })

        handoff_to = str(parsed.get("handoff_to", "")).strip()
        handoff_message = str(parsed.get("handoff_message", "")).strip()
        handoff = None
        if handoff_to:
            handoff = {
                "agent": handoff_to,
                "content": handoff_message or f"Handoff requested by {self.name}.",
            }

        artifacts: list[dict[str, Any]] = []
        for artifact in parsed.get("artifacts", []):
            if isinstance(artifact, dict):
                artifacts.append(copy.deepcopy(artifact))

        return {
            "agent": self.name,
            "publish": publish,
            "handoff": handoff,
            "final": str(parsed.get("final", "")).strip(),
            "idle": bool(parsed.get("idle", False)) and not publish and not handoff and not str(parsed.get("final", "")).strip(),
            "artifacts": artifacts,
            "conversation": conversation,
            "bundle": bundle,
            "raw_output": parsed,
        }

    def _run_step_with_prompt(
        self,
        *,
        coordination_prompt: str,
        user_prompt: str,
        payload: dict[str, Any] | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        max_iterations: int | None,
        session_id: str | None,
        memory_namespace: str | None,
        response_format: response_format,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        engine = self._build_engine()
        return engine.run(
            messages=self._compose_messages(
                [{"role": "user", "content": user_prompt}],
                extra_system_messages=[coordination_prompt],
            ),
            payload=self._merge_payload(payload),
            response_format=response_format,
            callback=callback,
            verbose=verbose,
            max_iterations=max_iterations,
            previous_response_id=None,
            on_tool_confirm=self.defaults.get("on_tool_confirm"),
            on_continuation_request=None,
            session_id=session_id,
            memory_namespace=memory_namespace,
        )

    def _format_envelopes(self, envelopes: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for item in envelopes:
            channel = str(item.get("channel", "") or "-")
            sender = str(item.get("sender", "") or "unknown")
            kind = str(item.get("kind", "") or "message")
            target = str(item.get("target", "") or "")
            mentions = item.get("mentions") if isinstance(item.get("mentions"), list) else []
            mention_text = f" mentions={','.join(str(x) for x in mentions if str(x).strip())}" if mentions else ""
            target_text = f" target={target}" if target else ""
            step = item.get("step")
            step_text = f"step={step}" if isinstance(step, int) else "step=-"
            content = as_text(item.get("content", "")).strip()
            lines.append(f"[{step_text}] channel={channel} sender={sender} kind={kind}{target_text}{mention_text}: {content}")
        return "\n".join(lines)

    def as_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Tool:
        tool_name = name or self.name
        tool_description = description or f"Delegate the task to agent '{self.name}'."

        def _delegate(task: str) -> dict[str, Any]:
            """Run a delegated task through the wrapped agent."""
            conversation, bundle = self.run(task)
            return {
                "agent": self.name,
                "output": self._extract_last_assistant_text(conversation),
                "bundle": bundle,
            }

        return Tool.from_callable(
            _delegate,
            name=tool_name,
            description=tool_description,
        )
__all__ = ["Agent", "Team"]


def __getattr__(name: str) -> Any:
    if name == "Team":
        from .team import Team

        return Team
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
