from __future__ import annotations

import copy
import inspect
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .._internal.agent_shared import as_text, normalize_mentions
from ..memory import LongTermMemoryConfig, MemoryConfig, MemoryManager
from ..runtime import Broth
from ..runtime.payloads import load_model_capabilities
from ..schemas import ResponseFormat
from ..tools import Tool, Toolkit
from ..tools.catalog import ToolkitCatalogConfig, extract_toolkit_catalog_token

_FORWARDED_BROTH_KEYS = {"provider", "model", "api_key", "memory_manager", "toolkit_catalog_config"}
_AGENT_MODEL_CAPABILITIES: dict[str, dict[str, Any]] | None = None

if TYPE_CHECKING:
    from .team import Team


def _deepcopy_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _make_step_response_format(agent_name: str) -> ResponseFormat:
    return ResponseFormat(
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


@dataclass
class _SubagentConfig:
    tool_name: str
    description: str | None
    max_depth: int
    max_children_per_agent: int
    max_total_subagents: int


@dataclass
class _SubagentCounters:
    total_created: int = 0
    direct_children: dict[tuple[str, ...], int] = field(default_factory=dict)


@dataclass
class _SubagentRuntime:
    config: _SubagentConfig
    current_depth: int
    lineage: tuple[str, ...]
    counters: _SubagentCounters
    current_session_id: str
    current_memory_namespace: str
    payload: dict[str, Any] | None
    callback: Callable[[dict[str, Any]], None] | None
    max_iterations: int | None
    verbose: bool
    on_tool_confirm: Callable | None
    on_continuation_request: Callable | None


def _slug_subagent_role(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", str(value or "").strip().lower())
    slug = re.sub(r"\.+", ".", slug).strip(".")
    return slug or "subagent"


def _join_instruction_parts(*parts: str) -> str:
    return "\n\n".join(part.strip() for part in parts if isinstance(part, str) and part.strip())


def _agent_model_capability_registry() -> dict[str, dict[str, Any]]:
    global _AGENT_MODEL_CAPABILITIES
    if _AGENT_MODEL_CAPABILITIES is None:
        try:
            _AGENT_MODEL_CAPABILITIES = load_model_capabilities()
        except Exception:
            _AGENT_MODEL_CAPABILITIES = {}
    return _AGENT_MODEL_CAPABILITIES


def _resolve_model_key(model: str, registry: dict[str, Any]) -> str | None:
    if model in registry:
        return model
    normalized_model = str(model or "").replace(".", "-")
    best: str | None = None
    for key in registry:
        normalized_key = str(key).replace(".", "-")
        if (
            str(model).startswith(key)
            or str(model).startswith(normalized_key)
            or normalized_model.startswith(key)
            or normalized_model.startswith(normalized_key)
            or key.startswith(str(model))
            or key.startswith(normalized_model)
            or normalized_key.startswith(str(model))
            or normalized_key.startswith(normalized_model)
        ) and (best is None or len(key) > len(best)):
            best = key
    return best


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
        toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None = None,
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
        self.toolkit_catalog_config = ToolkitCatalogConfig.coerce(toolkit_catalog_config)
        self.short_term_memory = short_term_memory
        self.long_term_memory = long_term_memory
        self.memory_manager = self._coerce_memory_manager(
            short_term_memory=short_term_memory,
            long_term_memory=long_term_memory,
        )
        self._subagent_config: _SubagentConfig | None = None
        self._suspended_toolkit_catalog_runs: dict[str, Any] = {}

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

    def enable_subagents(
        self,
        *,
        tool_name: str = "spawn_subagent",
        description: str | None = None,
        max_depth: int = 6,
        max_children_per_agent: int = 10,
        max_total_subagents: int = 100,
    ) -> Agent:
        tool_name = str(tool_name).strip()
        if not tool_name:
            raise ValueError("subagent tool_name must be a non-empty string")
        if int(max_depth) < 1:
            raise ValueError("subagent max_depth must be at least 1")
        if int(max_children_per_agent) < 1:
            raise ValueError("subagent max_children_per_agent must be at least 1")
        if int(max_total_subagents) < 1:
            raise ValueError("subagent max_total_subagents must be at least 1")

        self._subagent_config = _SubagentConfig(
            tool_name=tool_name,
            description=description,
            max_depth=int(max_depth),
            max_children_per_agent=int(max_children_per_agent),
            max_total_subagents=int(max_total_subagents),
        )
        return self

    def enable_toolkit_catalog(
        self,
        *,
        managed_toolkit_ids: tuple[str, ...] | list[str] | None,
        always_active_toolkit_ids: tuple[str, ...] | list[str] | None = None,
        registry: dict[str, Any] | None = None,
        readme_max_chars: int = 8000,
    ) -> Agent:
        self.toolkit_catalog_config = ToolkitCatalogConfig(
            managed_toolkit_ids=managed_toolkit_ids,
            always_active_toolkit_ids=always_active_toolkit_ids,
            registry=registry,
            readme_max_chars=readme_max_chars,
        )
        return self

    def _toolkit_catalog_state_token(self, continuation: dict[str, Any] | None) -> str | None:
        if not isinstance(continuation, dict):
            return None
        return extract_toolkit_catalog_token(continuation.get("toolkit_catalog"))

    def _restore_toolkit_catalog_state_to_engine(self, engine: Broth, continuation: dict[str, Any] | None) -> None:
        state_token = self._toolkit_catalog_state_token(continuation)
        if state_token is None:
            return
        runtime = self._suspended_toolkit_catalog_runs.pop(state_token, None)
        if runtime is not None:
            engine._put_suspended_toolkit_catalog_runtime(state_token, runtime)

    def _capture_toolkit_catalog_state_from_engine(self, engine: Broth, bundle: dict[str, Any] | None) -> None:
        if not isinstance(bundle, dict):
            return
        continuation = bundle.get("continuation")
        state_token = self._toolkit_catalog_state_token(continuation if isinstance(continuation, dict) else None)
        if state_token is None:
            return
        runtime = engine._take_suspended_toolkit_catalog_runtime(state_token)
        if runtime is not None:
            self._suspended_toolkit_catalog_runs[state_token] = runtime

    def _model_capability(self, key: str, default: Any = None) -> Any:
        registry = _agent_model_capability_registry()
        resolved = _resolve_model_key(self.model, registry)
        model_caps = registry.get(resolved, {}) if resolved else {}
        if not isinstance(model_caps, dict):
            return default
        return model_caps.get(key, default)

    def _supports_tool_calling(self) -> bool:
        return bool(self._model_capability("supports_tools", True))

    def _resolve_memory_tool_scope(
        self,
        *,
        runtime_context: _SubagentRuntime | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
    ) -> tuple[str, str | None]:
        resolved_session_id = session_id or ""
        resolved_memory_namespace = memory_namespace
        if runtime_context is not None:
            if not resolved_session_id:
                resolved_session_id = runtime_context.current_session_id
            if resolved_memory_namespace is None:
                resolved_memory_namespace = runtime_context.current_memory_namespace
        return resolved_session_id, resolved_memory_namespace

    def _build_memory_recall_toolkit(
        self,
        *,
        runtime_context: _SubagentRuntime | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
    ) -> Toolkit | None:
        if self.memory_manager is None or not self._supports_tool_calling():
            return None

        resolved_session_id, resolved_memory_namespace = self._resolve_memory_tool_scope(
            runtime_context=runtime_context,
            session_id=session_id,
            memory_namespace=memory_namespace,
        )
        toolkit = Toolkit()

        def _recall_profile(max_chars: int | None = None) -> dict[str, Any]:
            return self.memory_manager.recall_profile(
                session_id=resolved_session_id,
                memory_namespace=resolved_memory_namespace,
                max_chars=max_chars,
            )

        def _recall_memory(
            query: str,
            top_k: int | None = None,
            include_short_term: bool = True,
            include_long_term: bool = True,
        ) -> dict[str, Any]:
            return self.memory_manager.recall_memory(
                session_id=resolved_session_id,
                memory_namespace=resolved_memory_namespace,
                query=query,
                top_k=top_k,
                include_short_term=include_short_term,
                include_long_term=include_long_term,
            )

        toolkit.register(
            _recall_profile,
            name="recall_profile",
            description="Recall the long-term profile for the current memory namespace.",
            parameters=[
                {
                    "name": "max_chars",
                    "description": "Optional maximum serialized profile length.",
                    "type_": "integer",
                    "required": False,
                }
            ],
        )
        toolkit.register(
            _recall_memory,
            name="recall_memory",
            description=(
                "Recall additional short-term and long-term memory for the current query. "
                "This supplements the automatic memory recall path."
            ),
            parameters=[
                {
                    "name": "query",
                    "description": "The query to use for extra memory recall.",
                    "type_": "string",
                    "required": True,
                },
                {
                    "name": "top_k",
                    "description": "Optional override for recall top-k across memory sources.",
                    "type_": "integer",
                    "required": False,
                },
                {
                    "name": "include_short_term",
                    "description": "Whether to include additional short-term memory recall.",
                    "type_": "boolean",
                    "required": False,
                },
                {
                    "name": "include_long_term",
                    "description": "Whether to include additional long-term memory recall.",
                    "type_": "boolean",
                    "required": False,
                },
            ],
        )
        return toolkit

    def _build_agent_toolkit(
        self,
        *,
        runtime_context: _SubagentRuntime | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
    ) -> Toolkit:
        merged = Toolkit()
        internal_memory_toolkit = self._build_memory_recall_toolkit(
            runtime_context=runtime_context,
            session_id=session_id,
            memory_namespace=memory_namespace,
        )
        if internal_memory_toolkit is not None:
            for tool_obj in internal_memory_toolkit.tools.values():
                merged.register(tool_obj)
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
        if runtime_context is not None and self._subagent_config is not None:
            merged.register(self._build_subagent_tool(runtime_context))
        return merged

    def _build_engine(
        self,
        *,
        runtime_context: _SubagentRuntime | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
    ) -> Broth:
        core_kwargs: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "memory_manager": self.memory_manager,
            "toolkit_catalog_config": copy.deepcopy(self.toolkit_catalog_config),
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

        engine.toolkit = self._build_agent_toolkit(
            runtime_context=runtime_context,
            session_id=session_id,
            memory_namespace=memory_namespace,
        )

        default_confirm = self.defaults.get("on_tool_confirm")
        if callable(default_confirm):
            engine.on_tool_confirm = default_confirm
        return engine

    def _resolve_subagent_runtime(
        self,
        *,
        payload: dict[str, Any] | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        max_iterations: int | None,
        session_id: str | None,
        memory_namespace: str | None,
        on_tool_confirm: Callable | None,
        on_continuation_request: Callable | None,
        runtime_context: _SubagentRuntime | None,
    ) -> _SubagentRuntime | None:
        if runtime_context is not None:
            return runtime_context
        if self._subagent_config is None:
            return None

        runtime_session_id = session_id or str(uuid.uuid4())
        runtime_memory_namespace = memory_namespace or runtime_session_id
        return _SubagentRuntime(
            config=self._subagent_config,
            current_depth=0,
            lineage=(self.name,),
            counters=_SubagentCounters(),
            current_session_id=runtime_session_id,
            current_memory_namespace=runtime_memory_namespace,
            payload=copy.deepcopy(payload) if payload is not None else None,
            callback=callback,
            max_iterations=max_iterations,
            verbose=verbose,
            on_tool_confirm=on_tool_confirm or self.defaults.get("on_tool_confirm"),
            on_continuation_request=on_continuation_request,
        )

    def _compose_subagent_instructions(
        self,
        *,
        role: str,
        depth: int,
        instructions: str,
    ) -> str:
        overlay = f"""
You are a subagent created by parent agent "{self.name}".
Role: {role}
Depth: {depth}

Complete only the delegated task provided by your parent.
Return your result directly to the parent agent so it can continue coordinating the overall task.
""".strip()
        return _join_instruction_parts(self.instructions, overlay, instructions)

    def _make_subagent_error(
        self,
        *,
        message: str,
        agent_name: str,
        role: str,
        depth: int,
        lineage: tuple[str, ...],
    ) -> dict[str, Any]:
        return {
            "error": message,
            "agent": agent_name,
            "role": role,
            "depth": depth,
            "lineage": list(lineage),
        }

    def _clone_for_subagent(
        self,
        *,
        child_name: str,
        role: str,
        depth: int,
        instructions: str,
    ) -> Agent:
        child = Agent(
            name=child_name,
            instructions=self._compose_subagent_instructions(
                role=role,
                depth=depth,
                instructions=instructions,
            ),
            provider=self.provider,
            model=self.model,
            api_key=self.api_key,
            tools=list(self.tools),
            short_term_memory=self.memory_manager if self.memory_manager is not None else None,
            defaults=copy.deepcopy(self.defaults),
            broth_options=copy.deepcopy(self.broth_options),
            toolkit_catalog_config=copy.deepcopy(self.toolkit_catalog_config),
        )
        if self._subagent_config is not None:
            child.enable_subagents(
                tool_name=self._subagent_config.tool_name,
                description=self._subagent_config.description,
                max_depth=self._subagent_config.max_depth,
                max_children_per_agent=self._subagent_config.max_children_per_agent,
                max_total_subagents=self._subagent_config.max_total_subagents,
            )
        return child

    def _build_child_runtime(
        self,
        *,
        runtime_context: _SubagentRuntime,
        child_name: str,
    ) -> _SubagentRuntime:
        return _SubagentRuntime(
            config=runtime_context.config,
            current_depth=runtime_context.current_depth + 1,
            lineage=runtime_context.lineage + (child_name,),
            counters=runtime_context.counters,
            current_session_id=f"{runtime_context.current_session_id}:{child_name}",
            current_memory_namespace=f"{runtime_context.current_memory_namespace}:{child_name}",
            payload=copy.deepcopy(runtime_context.payload) if runtime_context.payload is not None else None,
            callback=runtime_context.callback,
            max_iterations=runtime_context.max_iterations,
            verbose=runtime_context.verbose,
            on_tool_confirm=runtime_context.on_tool_confirm,
            on_continuation_request=runtime_context.on_continuation_request,
        )

    def _spawn_subagent(
        self,
        *,
        runtime_context: _SubagentRuntime,
        task: str,
        role: str,
        instructions: str = "",
    ) -> dict[str, Any]:
        role_text = str(role or "").strip()
        task_text = str(task or "").strip()
        instructions_text = str(instructions or "").strip()
        next_depth = runtime_context.current_depth + 1

        if not role_text:
            return self._make_subagent_error(
                message="subagent role is required",
                agent_name=self.name,
                role="",
                depth=next_depth,
                lineage=runtime_context.lineage,
            )
        if not task_text:
            return self._make_subagent_error(
                message="subagent task is required",
                agent_name=self.name,
                role=role_text,
                depth=next_depth,
                lineage=runtime_context.lineage,
            )

        config = runtime_context.config
        if next_depth > config.max_depth:
            return self._make_subagent_error(
                message=f"subagent max_depth exceeded: attempted depth {next_depth} > {config.max_depth}",
                agent_name=self.name,
                role=role_text,
                depth=next_depth,
                lineage=runtime_context.lineage,
            )

        lineage_key = runtime_context.lineage
        current_children = runtime_context.counters.direct_children.get(lineage_key, 0)
        if current_children >= config.max_children_per_agent:
            return self._make_subagent_error(
                message=(
                    "subagent max_children_per_agent exceeded: "
                    f"attempted child {current_children + 1} > {config.max_children_per_agent}"
                ),
                agent_name=self.name,
                role=role_text,
                depth=next_depth,
                lineage=runtime_context.lineage,
            )
        if runtime_context.counters.total_created >= config.max_total_subagents:
            return self._make_subagent_error(
                message=(
                    "subagent max_total_subagents exceeded: "
                    f"attempted child {runtime_context.counters.total_created + 1} > {config.max_total_subagents}"
                ),
                agent_name=self.name,
                role=role_text,
                depth=next_depth,
                lineage=runtime_context.lineage,
            )

        child_index = current_children + 1
        child_name = f"{self.name}.{_slug_subagent_role(role_text)}.{child_index}"
        runtime_context.counters.direct_children[lineage_key] = child_index
        runtime_context.counters.total_created += 1

        child_runtime = self._build_child_runtime(
            runtime_context=runtime_context,
            child_name=child_name,
        )
        child = self._clone_for_subagent(
            child_name=child_name,
            role=role_text,
            depth=child_runtime.current_depth,
            instructions=instructions_text,
        )

        try:
            conversation, bundle = child._run_internal(
                task_text,
                payload=copy.deepcopy(child_runtime.payload) if child_runtime.payload is not None else None,
                response_format=None,
                callback=child_runtime.callback,
                verbose=child_runtime.verbose,
                max_iterations=child_runtime.max_iterations,
                previous_response_id=None,
                on_tool_confirm=child_runtime.on_tool_confirm,
                on_continuation_request=child_runtime.on_continuation_request,
                session_id=child_runtime.current_session_id,
                memory_namespace=child_runtime.current_memory_namespace,
                extra_system_messages=None,
                runtime_context=child_runtime,
            )
        except Exception as exc:
            return self._make_subagent_error(
                message=str(exc),
                agent_name=child_name,
                role=role_text,
                depth=child_runtime.current_depth,
                lineage=child_runtime.lineage,
            )

        return {
            "agent": child_name,
            "role": role_text,
            "depth": child_runtime.current_depth,
            "lineage": list(child_runtime.lineage),
            "output": child._extract_last_assistant_text(conversation),
            "bundle": bundle,
        }

    def _build_subagent_tool(self, runtime_context: _SubagentRuntime) -> Tool:
        config = runtime_context.config

        def _delegate_subagent(task: str, role: str, instructions: str = "") -> dict[str, Any]:
            """Run a delegated task through a dynamically created child agent.

            Args:
                task: The delegated task for the child agent.
                role: The role the child agent should take on.
                instructions: Extra instructions appended to the child agent prompt.
            """
            return self._spawn_subagent(
                runtime_context=runtime_context,
                task=task,
                role=role,
                instructions=instructions,
            )

        return Tool.from_callable(
            _delegate_subagent,
            name=config.tool_name,
            description=config.description or "Create a child agent, run a delegated task, and return the result.",
        )

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
        response_format: ResponseFormat | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        max_iterations: int | None = None,
        previous_response_id: str | None = None,
        on_tool_confirm: Callable | None = None,
        on_continuation_request: Callable | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return self._run_internal(
            messages,
            payload=payload,
            response_format=response_format,
            callback=callback,
            verbose=verbose,
            max_iterations=max_iterations,
            previous_response_id=previous_response_id,
            on_tool_confirm=on_tool_confirm,
            on_continuation_request=on_continuation_request,
            session_id=session_id,
            memory_namespace=memory_namespace,
            extra_system_messages=None,
            runtime_context=None,
        )

    def _run_internal(
        self,
        messages: str | list[dict[str, Any]] | None,
        *,
        payload: dict[str, Any] | None,
        response_format: ResponseFormat | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        max_iterations: int | None,
        previous_response_id: str | None,
        on_tool_confirm: Callable | None,
        on_continuation_request: Callable | None,
        session_id: str | None,
        memory_namespace: str | None,
        extra_system_messages: list[str] | None,
        runtime_context: _SubagentRuntime | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        resolved_runtime = self._resolve_subagent_runtime(
            payload=payload,
            callback=callback,
            verbose=verbose,
            max_iterations=max_iterations,
            session_id=session_id,
            memory_namespace=memory_namespace,
            on_tool_confirm=on_tool_confirm,
            on_continuation_request=on_continuation_request,
            runtime_context=runtime_context,
        )
        engine = self._build_engine(
            runtime_context=resolved_runtime,
            session_id=(
                resolved_runtime.current_session_id if resolved_runtime is not None else session_id
            ),
            memory_namespace=(
                resolved_runtime.current_memory_namespace if resolved_runtime is not None else memory_namespace
            ),
        )
        conversation_out, bundle = engine.run(
            messages=self._compose_messages(messages, extra_system_messages=extra_system_messages),
            payload=self._merge_payload(payload),
            response_format=response_format or self.defaults.get("response_format"),
            callback=callback,
            verbose=verbose,
            max_iterations=max_iterations,
            previous_response_id=previous_response_id,
            on_tool_confirm=(
                resolved_runtime.on_tool_confirm if resolved_runtime is not None
                else on_tool_confirm or self.defaults.get("on_tool_confirm")
            ),
            on_continuation_request=(
                resolved_runtime.on_continuation_request if resolved_runtime is not None
                else on_continuation_request
            ),
            session_id=session_id,
            memory_namespace=memory_namespace,
        )
        self._capture_toolkit_catalog_state_from_engine(engine, bundle)
        return conversation_out, bundle

    def resume_human_input(
        self,
        *,
        conversation: list[dict[str, Any]],
        continuation: dict[str, Any],
        response: dict[str, Any] | Any,
        payload: dict[str, Any] | None = None,
        response_format: ResponseFormat | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        on_tool_confirm: Callable | None = None,
        on_continuation_request: Callable | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return self._resume_human_input_internal(
            conversation=conversation,
            continuation=continuation,
            response=response,
            payload=payload,
            response_format=response_format,
            callback=callback,
            verbose=verbose,
            on_tool_confirm=on_tool_confirm,
            on_continuation_request=on_continuation_request,
            session_id=session_id,
            memory_namespace=memory_namespace,
            runtime_context=None,
        )

    def _resume_human_input_internal(
        self,
        *,
        conversation: list[dict[str, Any]],
        continuation: dict[str, Any],
        response: dict[str, Any] | Any,
        payload: dict[str, Any] | None,
        response_format: ResponseFormat | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        on_tool_confirm: Callable | None,
        on_continuation_request: Callable | None,
        session_id: str | None,
        memory_namespace: str | None,
        runtime_context: _SubagentRuntime | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        resolved_runtime = self._resolve_subagent_runtime(
            payload=payload,
            callback=callback,
            verbose=verbose,
            max_iterations=None,
            session_id=session_id,
            memory_namespace=memory_namespace,
            on_tool_confirm=on_tool_confirm,
            on_continuation_request=on_continuation_request,
            runtime_context=runtime_context,
        )
        engine = self._build_engine(
            runtime_context=resolved_runtime,
            session_id=(
                resolved_runtime.current_session_id if resolved_runtime is not None else session_id
            ),
            memory_namespace=(
                resolved_runtime.current_memory_namespace if resolved_runtime is not None else memory_namespace
            ),
        )
        self._restore_toolkit_catalog_state_to_engine(engine, continuation)
        conversation_out, bundle = engine.resume_human_input(
            conversation=copy.deepcopy(conversation),
            continuation=copy.deepcopy(continuation),
            response=copy.deepcopy(response),
            payload=self._merge_payload(payload) if payload is not None else None,
            response_format=response_format or self.defaults.get("response_format"),
            callback=callback,
            verbose=verbose,
            on_tool_confirm=(
                resolved_runtime.on_tool_confirm if resolved_runtime is not None
                else on_tool_confirm or self.defaults.get("on_tool_confirm")
            ),
            on_continuation_request=(
                resolved_runtime.on_continuation_request if resolved_runtime is not None
                else on_continuation_request
            ),
            session_id=session_id,
            memory_namespace=memory_namespace,
        )
        self._capture_toolkit_catalog_state_from_engine(engine, bundle)
        return conversation_out, bundle

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
        response_format: ResponseFormat,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return self._run_internal(
            [{"role": "user", "content": user_prompt}],
            payload=payload,
            response_format=response_format,
            callback=callback,
            verbose=verbose,
            max_iterations=max_iterations,
            previous_response_id=None,
            on_tool_confirm=self.defaults.get("on_tool_confirm"),
            on_continuation_request=None,
            session_id=session_id,
            memory_namespace=memory_namespace,
            extra_system_messages=[coordination_prompt],
            runtime_context=None,
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
