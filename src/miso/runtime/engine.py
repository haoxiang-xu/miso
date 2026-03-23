from __future__ import annotations

import base64
import copy
import inspect
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..input.human_input import (
    ASK_USER_QUESTION_TOOL_NAME,
    HumanInputRequest,
    HumanInputResponse,
    is_human_input_tool_name,
)
from ..memory import InMemorySessionStore, MemoryManager
from ..schemas import ResponseFormat
from ..tools.catalog import (
    ToolkitCatalogConfig,
    ToolkitCatalogRuntime,
    build_visible_toolkits,
    extract_toolkit_catalog_token,
)
from ..tools.models import ToolConfirmationRequest, ToolConfirmationResponse
from ..tools.toolkit import Toolkit as BaseToolkit
from ..workspace.pins import WorkspacePinExecutionContext, build_pinned_prompt_messages
from . import providers
from .files import resolve_openai_file_upload
from .payloads import (
    DEFAULT_PAYLOADS_RESOURCE,
    MODEL_CAPABILITIES_RESOURCE,
    load_default_payloads,
    load_model_capabilities,
)

try:
    from IPython.display import clear_output
except Exception:  # pragma: no cover
    clear_output = None

OBSERVATION_SYSTEM_PROMPT = """
You are a critical reviewer embedded in a multi-step AI agent pipeline.
You will receive recent conversation context and the results of one or more tool calls.
Your job is to review the LAST tool call result and provide a brief, actionable observation.

Check:
1. Does the result contain errors or warnings? If so, what specifically went wrong?
2. Are the returned values consistent with what was requested? (e.g. column names match, row counts make sense, no nulls where values were expected)
3. Is there anything the main assistant is likely to overlook or misinterpret in the next step?
4. Based on this result, what is the single most important thing to do or avoid next?

Rules:
- Be concise: 2-4 sentences maximum.
- Be specific: reference actual column names, values, error messages from the result.
- Do NOT repeat the result data — only comment on it.
- If everything looks correct, say so in one sentence and suggest the next logical action.
""".strip()

OBSERVATION_RECENT_MESSAGES = 6
OBSERVATION_MAX_OUTPUT_TOKENS = 512
DEFAULT_PAYLOADS_FILE = DEFAULT_PAYLOADS_RESOURCE
MODEL_CAPABILITIES_FILE = MODEL_CAPABILITIES_RESOURCE

@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] | str | None

@dataclass
class ProviderTurnResult:
    assistant_messages: list[dict[str, Any]]
    tool_calls: list[ToolCall]
    final_text: str = ""
    response_id: str | None = None
    reasoning_items: list[dict[str, Any]] | None = None
    consumed_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class TokenUsage:
    consumed_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolExecutionOutcome:
    result_messages: list[dict[str, Any]]
    should_observe: bool = False
    awaiting_human_input: bool = False
    human_input_request: HumanInputRequest | None = None

    def __iter__(self):
        yield self.result_messages
        yield self.should_observe

class Broth:
    def __init__(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        memory_manager: MemoryManager | None = None,
        toolkit_catalog_config: ToolkitCatalogConfig | dict[str, Any] | None = None,
    ):
        self.api_key = api_key
        self.provider = provider or "openai"
        self.model = model or "gpt-5"
        self.max_iterations = 6
        self.default_payload = self._load_default_payloads(DEFAULT_PAYLOADS_FILE)
        self.model_capabilities = self._load_model_capabilities(MODEL_CAPABILITIES_FILE)
        self.toolkits: list[BaseToolkit] = []
        self.on_tool_confirm: Callable | None = None
        self.last_response_id: str | None = None
        self.last_reasoning_items: list[dict[str, Any]] = []
        self.last_consumed_tokens: int = 0
        self.consumed_tokens: int = 0
        self._max_context_window_tokens: int | None = None
        # sha256(base64_data) -> openai file_id — shared across run() calls
        self._file_id_cache: dict[str, str] = {}
        # openai file_id -> sha256 (reverse map for stale-id retry)
        self._file_id_reverse: dict[str, str] = {}
        # canonical seed messages for the current run (used in stale-file retry)
        self._last_canonical_seed: list[dict[str, Any]] = []
        self.memory_manager = memory_manager
        self._workspace_pin_store: InMemorySessionStore | None = None
        self.toolkit_catalog_config = ToolkitCatalogConfig.coerce(toolkit_catalog_config)
        self._suspended_toolkit_catalog_runs: dict[str, ToolkitCatalogRuntime] = {}

    def _call_with_supported_kwargs(self, func: Callable[..., Any], **kwargs: Any) -> Any:
        """Call a possibly monkey-patched callable, omitting unsupported kwargs."""
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return func(**kwargs)

        if any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        ):
            return func(**kwargs)

        filtered_kwargs = {
            key: value for key, value in kwargs.items() if key in signature.parameters
        }
        return func(**filtered_kwargs)

    def _prepare_memory_messages(
        self,
        *,
        session_id: str,
        incoming: list[dict[str, Any]],
        max_context_window_tokens: int,
        model: str,
        summary_generator: Callable[..., Any] | None = None,
        memory_namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.memory_manager is None:
            return incoming
        return self._call_with_supported_kwargs(
            self.memory_manager.prepare_messages,
            session_id=session_id,
            incoming=incoming,
            max_context_window_tokens=max_context_window_tokens,
            model=model,
            summary_generator=summary_generator,
            memory_namespace=memory_namespace,
            provider=self.provider,
            tool_resolver=self._find_tool,
        )

    def _commit_memory_messages(
        self,
        *,
        session_id: str,
        full_conversation: list[dict[str, Any]],
        memory_namespace: str | None = None,
        model: str | None = None,
        long_term_extractor: Callable[..., Any] | None = None,
    ) -> None:
        if self.memory_manager is None:
            return
        self._call_with_supported_kwargs(
            self.memory_manager.commit_messages,
            session_id=session_id,
            full_conversation=full_conversation,
            memory_namespace=memory_namespace,
            model=model,
            long_term_extractor=long_term_extractor,
        )

    def _get_workspace_pin_store(self):
        if self.memory_manager is not None and hasattr(self.memory_manager, "store"):
            return self.memory_manager.store
        if self._workspace_pin_store is None:
            self._workspace_pin_store = InMemorySessionStore()
        return self._workspace_pin_store

    def _make_workspace_pin_context(
        self,
        *,
        session_id: str | None,
    ) -> WorkspacePinExecutionContext | None:
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        return WorkspacePinExecutionContext(
            session_id=session_id,
            session_store=self._get_workspace_pin_store(),
        )

    def _inject_workspace_pin_messages(
        self,
        *,
        messages: list[dict[str, Any]],
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        context = self._make_workspace_pin_context(session_id=session_id)
        if context is None:
            return copy.deepcopy(messages)

        pin_messages = build_pinned_prompt_messages(
            store=context.session_store,
            session_id=context.session_id,
        )
        if not pin_messages:
            return copy.deepcopy(messages)

        systems: list[dict[str, Any]] = []
        non_system: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "system":
                systems.append(copy.deepcopy(message))
            else:
                non_system.append(copy.deepcopy(message))
        return systems + pin_messages + non_system

    @property
    def toolkit(self) -> BaseToolkit:
        """Return a merged view of all registered toolkits.

        Setting this property replaces the entire toolkits list with a single
        toolkit.
        """
        merged = BaseToolkit()
        for tk in self.toolkits:
            merged.tools.update(tk.tools)
        return merged

    @toolkit.setter
    def toolkit(self, value: BaseToolkit) -> None:
        self.toolkits = [value]

    # ── multi-toolkit management ───────────────────────────────────────────

    def add_toolkit(self, tk: BaseToolkit) -> None:
        """Append a toolkit to the agent's toolkit list."""
        self.toolkits.append(tk)

    def remove_toolkit(self, tk: BaseToolkit) -> None:
        """Remove a toolkit from the agent's toolkit list."""
        self.toolkits.remove(tk)

    # ── internal helpers for merged tool lookup ────────────────────────────

    def _merged_tools_json(self) -> list[dict[str, Any]]:
        """Build a combined tool-definition list from all registered toolkits."""
        merged: dict[str, dict[str, Any]] = {}
        for tk in self.toolkits:
            for t in tk.to_json():
                merged[t.get("name", "")] = t
        return list(merged.values())

    def _find_tool(self, name: str, *, toolkits: list[BaseToolkit] | None = None):
        """Look up a tool by name across all toolkits (last registered wins)."""
        result = None
        for tk in toolkits or self.toolkits:
            found = tk.get(name)
            if found is not None:
                result = found
        return result

    def _execute_from_toolkits(
        self,
        name: str,
        arguments,
        *,
        session_id: str | None = None,
        toolkits: list[BaseToolkit] | None = None,
    ) -> dict[str, Any]:
        """Execute a tool by name, searching all toolkits (last registered wins)."""
        target_toolkit = None
        for tk in toolkits or self.toolkits:
            if tk.get(name) is not None:
                target_toolkit = tk
        if target_toolkit is None:
            return {"error": f"tool not found: {name}", "tool": name}
        context = self._make_workspace_pin_context(session_id=session_id)
        pushed_context = False
        if context is not None and hasattr(target_toolkit, "push_execution_context"):
            target_toolkit.push_execution_context(context)
            pushed_context = True
        try:
            return target_toolkit.execute(name, arguments)
        finally:
            if pushed_context and hasattr(target_toolkit, "pop_execution_context"):
                target_toolkit.pop_execution_context()

    def _build_runtime_toolkit(self, *, toolkits: list[BaseToolkit] | None = None) -> BaseToolkit:
        runtime_toolkit = BaseToolkit()
        for tk in toolkits or self.toolkits:
            for tool_obj in tk.tools.values():
                runtime_toolkit.register(tool_obj)
        return runtime_toolkit

    def _build_toolkit_catalog_runtime(self) -> ToolkitCatalogRuntime | None:
        if self.toolkit_catalog_config is None:
            return None
        return ToolkitCatalogRuntime(
            config=self.toolkit_catalog_config,
            eager_toolkits=list(self.toolkits),
        )

    def _stash_suspended_toolkit_catalog_runtime(self, runtime: ToolkitCatalogRuntime) -> dict[str, Any]:
        payload = runtime.build_continuation_state()
        state_token = extract_toolkit_catalog_token(payload)
        if state_token:
            self._suspended_toolkit_catalog_runs[state_token] = runtime
        return payload

    def _take_suspended_toolkit_catalog_runtime(self, state_token: str) -> ToolkitCatalogRuntime | None:
        return self._suspended_toolkit_catalog_runs.pop(state_token, None)

    def _put_suspended_toolkit_catalog_runtime(
        self,
        state_token: str,
        runtime: ToolkitCatalogRuntime,
    ) -> None:
        if state_token:
            self._suspended_toolkit_catalog_runs[state_token] = runtime

    def _restore_toolkit_catalog_runtime(
        self,
        continuation: dict[str, Any],
    ) -> ToolkitCatalogRuntime | None:
        catalog_payload = continuation.get("toolkit_catalog")
        if not isinstance(catalog_payload, dict):
            return None

        state_token = extract_toolkit_catalog_token(catalog_payload)
        if state_token is None:
            raise ValueError("continuation toolkit_catalog payload is missing a valid state_token")

        runtime = self._take_suspended_toolkit_catalog_runtime(state_token)
        if runtime is None:
            raise ValueError(
                "toolkit catalog continuation state is unavailable in this process; "
                "the suspended run cannot be resumed"
            )
        return runtime

    def _validate_runtime_toolkit_support(self, runtime_toolkit: BaseToolkit) -> None:
        has_human_input_tool = runtime_toolkit.get(ASK_USER_QUESTION_TOOL_NAME) is not None
        supports_tools = bool(self._model_capability("supports_tools", True))
        if has_human_input_tool and not supports_tools:
            raise ValueError(
                "AskUserToolkit requires a tool-calling model; "
                f"model '{self.model}' does not support tools and automatic fallback is not supported."
            )

    def _serialize_response_format(
        self,
        fmt: ResponseFormat | None,
    ) -> dict[str, Any] | None:
        if fmt is None:
            return None
        return {
            "name": fmt.name,
            "schema": copy.deepcopy(fmt.schema),
            "required": list(fmt.required),
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

    def _build_tool_message(
        self,
        *,
        tool_call: ToolCall,
        tool_result: dict[str, Any],
    ) -> dict[str, Any]:
        content = json.dumps(tool_result, default=str, ensure_ascii=False)

        if self.provider == "openai":
            return {
                "type": "function_call_output",
                "call_id": tool_call.call_id,
                "output": content,
            }
        if self.provider == "anthropic":
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call.call_id,
                    "content": content,
                }],
            }
        if self.provider == "gemini":
            try:
                response_val = json.loads(content) if content.strip() else {}
            except (json.JSONDecodeError, ValueError):
                response_val = {"result": content}
            return {
                "role": "user",
                "parts": [{
                    "function_response": {
                        "name": tool_call.name,
                        "response": response_val if isinstance(response_val, dict) else {"result": response_val},
                    },
                }],
            }
        return {
            "role": "tool",
            "tool_call_id": tool_call.call_id,
            "content": content,
        }

    def _finalize_memory_commit(
        self,
        *,
        callback: Callable[[dict[str, Any]], None] | None,
        run_id: str,
        iteration: int,
        conversation: list[dict[str, Any]],
        session_id: str | None,
        memory_namespace: str | None,
    ) -> None:
        if self.memory_manager is None or not session_id:
            return
        try:
            self._commit_memory_messages(
                session_id=session_id,
                full_conversation=conversation,
                memory_namespace=memory_namespace,
                model=self.model,
                long_term_extractor=self._extract_long_term_memory,
            )
            memory_commit_info = {
                **self.memory_manager.last_commit_info,
                "applied": True,
            }
            self._emit(
                callback,
                "memory_commit",
                run_id,
                iteration=iteration,
                **memory_commit_info,
            )
        except Exception as exc:
            self._emit(
                callback,
                "memory_commit",
                run_id,
                iteration=iteration,
                session_id=session_id,
                applied=False,
                fallback_reason=f"memory_commit_failed: {exc}",
            )

    def _load_default_payloads(self, path: str | Path) -> dict[str, dict[str, Any]]:
        del path
        return load_default_payloads()

    def _load_model_capabilities(self, path: str | Path) -> dict[str, dict[str, Any]]:
        del path
        return load_model_capabilities()

    def _resolve_model_key(self, registry: dict[str, Any]) -> str | None:
        """Resolve self.model to the best matching key in *registry*.

        Returns ``self.model`` verbatim when it exists in the registry.
        Otherwise, finds the longest registered key that is a prefix of
        ``self.model``, so that e.g. ``claude-opus-4-20250514`` matches
        the ``claude-opus-4`` entry.  Returns ``None`` when no key matches.
        """
        if self.model in registry:
            return self.model
        normalized_model = self.model.replace(".", "-")
        best: str | None = None
        for key in registry:
            normalized_key = key.replace(".", "-")
            if (
                self.model.startswith(key)
                or self.model.startswith(normalized_key)
                or normalized_model.startswith(key)
                or normalized_model.startswith(normalized_key)
                or key.startswith(self.model)
                or key.startswith(normalized_model)
                or normalized_key.startswith(self.model)
                or normalized_key.startswith(normalized_model)
            ) and (best is None or len(key) > len(best)):
                best = key
        return best

    def _model_capability(self, key: str, default: Any = None) -> Any:
        resolved = self._resolve_model_key(self.model_capabilities)
        model_caps = self.model_capabilities.get(resolved, {}) if resolved else {}
        if not isinstance(model_caps, dict):
            return default
        return model_caps.get(key, default)

    @property
    def max_context_window_tokens(self) -> int:
        """Return the context window token limit.

        Uses the user-specified value if set, otherwise falls back to the
        model's default from capabilities config.
        """
        if self._max_context_window_tokens is not None:
            return self._max_context_window_tokens
        return int(self._model_capability("max_context_window_tokens", 0))

    @max_context_window_tokens.setter
    def max_context_window_tokens(self, value: int | None) -> None:
        self._max_context_window_tokens = value

    def _build_bundle(
        self,
        run_consumed: int,
        last_turn_tokens: int,
        *,
        status: str,
        run_input_tokens: int = 0,
        run_output_tokens: int = 0,
        human_input_request: dict[str, Any] | None = None,
        continuation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        max_ctx = self.max_context_window_tokens
        pct = (last_turn_tokens / max_ctx * 100.0) if max_ctx > 0 else 0.0
        return {
            "model": self.model,
            "consumed_tokens": run_consumed,
            "input_tokens": max(0, int(run_input_tokens or 0)),
            "output_tokens": max(0, int(run_output_tokens or 0)),
            "max_context_window_tokens": max_ctx,
            "context_window_used_pct": round(pct, 2),
            "status": status,
            "human_input_request": copy.deepcopy(human_input_request),
            "continuation": copy.deepcopy(continuation),
        }

    def _build_human_input_continuation(
        self,
        *,
        request: HumanInputRequest,
        payload: dict[str, Any],
        response_format_obj: ResponseFormat | None,
        iteration: int,
        max_iterations: int,
        previous_response_id: str | None,
        use_openai_previous_response_chain: bool,
        session_id: str | None,
        memory_namespace: str | None,
        consumed_tokens: int,
        last_turn_tokens: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        last_turn_input_tokens: int = 0,
        last_turn_output_tokens: int = 0,
        toolkit_catalog: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "human_input_continuation",
            "kind": request.kind,
            "provider": self.provider,
            "model": self.model,
            "request_id": request.request_id,
            "call_id": request.request_id,
            "request": request.to_dict(),
            "payload": copy.deepcopy(payload),
            "response_format": self._serialize_response_format(response_format_obj),
            "iteration": iteration,
            "max_iterations": max_iterations,
            "previous_response_id": previous_response_id,
            "use_openai_previous_response_chain": use_openai_previous_response_chain,
            "session_id": session_id,
            "memory_namespace": memory_namespace,
            "consumed_tokens": consumed_tokens,
            "input_tokens": max(0, int(input_tokens or 0)),
            "output_tokens": max(0, int(output_tokens or 0)),
            "last_turn_tokens": last_turn_tokens,
            "last_turn_input_tokens": max(0, int(last_turn_input_tokens or 0)),
            "last_turn_output_tokens": max(0, int(last_turn_output_tokens or 0)),
            "toolkit_catalog": copy.deepcopy(toolkit_catalog),
        }

    def _coerce_token_count(self, value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _normalize_token_usage(
        self,
        *,
        consumed_tokens: Any = None,
        input_tokens: Any = None,
        output_tokens: Any = None,
    ) -> TokenUsage:
        input_count = self._coerce_token_count(input_tokens)
        output_count = self._coerce_token_count(output_tokens)
        consumed_count = self._coerce_token_count(consumed_tokens)
        if consumed_count == 0 and (input_count > 0 or output_count > 0):
            consumed_count = input_count + output_count
        return TokenUsage(
            consumed_tokens=consumed_count,
            input_tokens=input_count,
            output_tokens=output_count,
        )

    def _canonicalize_seed_messages(self, messages) -> list[dict[str, Any]]:
        canonical: list[dict[str, Any]] = []
        for index, message in enumerate(messages or []):
            if not isinstance(message, dict):
                raise ValueError(
                    f"error: messages[{index}] must be a dict. "
                    "( runtime.engine -> _canonicalize_seed_messages )"
                )

            # Keep non-chat OpenAI response items untouched for compatibility.
            if "role" not in message:
                canonical.append(copy.deepcopy(message))
                continue

            normalized = copy.deepcopy(message)
            normalized["content"] = self._canonicalize_content_blocks(
                role=str(normalized.get("role", "")),
                content=normalized.get("content", ""),
            )
            canonical.append(normalized)
        return canonical

    def _canonicalize_content_blocks(self, role: str, content: Any) -> list[dict[str, Any]] | str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            raise ValueError(
                "error: message content must be string or list of blocks. "
                "( runtime.engine -> _canonicalize_content_blocks )"
            )

        canonical_blocks = self._detect_and_convert_provider_native_blocks(self.provider, content)
        if role == "system":
            text_parts: list[str] = []
            for block in canonical_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    text_parts.append(text if isinstance(text, str) else str(text))
                    continue
                raise ValueError(
                    "error: system content blocks only support text. "
                    "( runtime.engine -> _canonicalize_content_blocks )"
                )
            return "".join(text_parts)
        return canonical_blocks

    def _detect_and_convert_provider_native_blocks(
        self,
        provider: str,
        blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        del provider  # reserved for provider-specific conversion behavior
        converted: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError(
                    "error: content block must be an object. "
                    "( runtime.engine -> _detect_and_convert_provider_native_blocks )"
                )
            block_type = block.get("type")

            if block_type in ("text", "input_text"):
                text = block.get("text", "")
                if not isinstance(text, str):
                    raise ValueError(
                        "error: text block requires string field 'text'. "
                        "( runtime.engine -> _detect_and_convert_provider_native_blocks )"
                    )
                converted.append({"type": "text", "text": text})
                continue

            if block_type in ("image", "input_image"):
                converted.append(self._canonicalize_image_block(block, str(block_type)))
                continue

            if block_type in ("pdf", "document", "input_file"):
                converted.append(self._canonicalize_pdf_block(block, str(block_type)))
                continue

            converted.append(copy.deepcopy(block))

        return converted

    def _canonicalize_image_block(self, block: dict[str, Any], block_type: str) -> dict[str, Any]:
        if block_type == "input_image":
            image_url = block.get("image_url")
            if not isinstance(image_url, str) or not image_url.strip():
                raise ValueError(
                    "error: input_image requires non-empty string field 'image_url'. "
                    "( runtime.engine -> _canonicalize_image_block )"
                )

            parsed = self._parse_data_url(image_url)
            if parsed is not None:
                media_type, data = parsed
                if not media_type.startswith("image/"):
                    raise ValueError(
                        "error: image base64 media_type must start with 'image/'. "
                        "( runtime.engine -> _canonicalize_image_block )"
                    )
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }

            return {
                "type": "image",
                "source": {
                    "type": "url",
                    "url": image_url,
                },
            }

        source = block.get("source")
        if not isinstance(source, dict):
            raise ValueError(
                "error: image block requires object field 'source'. "
                "( runtime.engine -> _canonicalize_image_block )"
            )
        return {
            "type": "image",
            "source": self._canonicalize_image_source(source),
        }

    def _canonicalize_pdf_block(self, block: dict[str, Any], block_type: str) -> dict[str, Any]:
        if block_type == "input_file":
            file_id = block.get("file_id")
            if isinstance(file_id, str) and file_id.strip():
                return {
                    "type": "pdf",
                    "source": {
                        "type": "file_id",
                        "file_id": file_id,
                    },
                }

            file_url = block.get("file_url")
            if isinstance(file_url, str) and file_url.strip():
                return {
                    "type": "pdf",
                    "source": {
                        "type": "url",
                        "url": file_url,
                    },
                }

            file_data = block.get("file_data")
            if isinstance(file_data, str) and file_data.strip():
                media_type = block.get("media_type", "application/pdf")
                if media_type != "application/pdf":
                    raise ValueError(
                        "error: pdf base64 media_type must be 'application/pdf'. "
                        "( runtime.engine -> _canonicalize_pdf_block )"
                    )
                source: dict[str, Any] = {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": file_data,
                }
                filename = block.get("filename")
                if isinstance(filename, str) and filename.strip():
                    source["filename"] = filename
                return {
                    "type": "pdf",
                    "source": source,
                }

            raise ValueError(
                "error: input_file requires 'file_id', 'file_url', or 'file_data'. "
                "( runtime.engine -> _canonicalize_pdf_block )"
            )

        source = block.get("source")
        if not isinstance(source, dict):
            raise ValueError(
                "error: pdf block requires object field 'source'. "
                "( runtime.engine -> _canonicalize_pdf_block )"
            )
        return {
            "type": "pdf",
            "source": self._canonicalize_pdf_source(source),
        }

    def _canonicalize_image_source(self, source: dict[str, Any]) -> dict[str, Any]:
        source_type = source.get("type")
        if source_type == "url":
            url = source.get("url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError(
                    "error: image url source requires non-empty string 'url'. "
                    "( runtime.engine -> _canonicalize_image_source )"
                )
            return {"type": "url", "url": url}

        if source_type == "base64":
            data = source.get("data")
            media_type = source.get("media_type")
            if not isinstance(data, str) or not data.strip():
                raise ValueError(
                    "error: image base64 source requires non-empty string 'data'. "
                    "( runtime.engine -> _canonicalize_image_source )"
                )
            if not isinstance(media_type, str) or not media_type.startswith("image/"):
                raise ValueError(
                    "error: image base64 media_type must start with 'image/'. "
                    "( runtime.engine -> _canonicalize_image_source )"
                )
            return {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            }

        raise ValueError(
            "error: image source.type must be 'url' or 'base64'. "
            "( runtime.engine -> _canonicalize_image_source )"
        )

    def _canonicalize_pdf_source(self, source: dict[str, Any]) -> dict[str, Any]:
        source_type = source.get("type")
        if source_type == "url":
            url = source.get("url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError(
                    "error: pdf url source requires non-empty string 'url'. "
                    "( runtime.engine -> _canonicalize_pdf_source )"
                )
            return {"type": "url", "url": url}

        if source_type == "base64":
            data = source.get("data")
            media_type = source.get("media_type")
            if not isinstance(data, str) or not data.strip():
                raise ValueError(
                    "error: pdf base64 source requires non-empty string 'data'. "
                    "( runtime.engine -> _canonicalize_pdf_source )"
                )
            if media_type != "application/pdf":
                raise ValueError(
                    "error: pdf base64 media_type must be 'application/pdf'. "
                    "( runtime.engine -> _canonicalize_pdf_source )"
                )
            normalized: dict[str, Any] = {
                "type": "base64",
                "media_type": "application/pdf",
                "data": data,
            }
            filename = source.get("filename")
            if isinstance(filename, str) and filename.strip():
                normalized["filename"] = filename
            return normalized

        if source_type == "file_id":
            file_id = source.get("file_id")
            if not isinstance(file_id, str) or not file_id.strip():
                raise ValueError(
                    "error: pdf file_id source requires non-empty string 'file_id'. "
                    "( runtime.engine -> _canonicalize_pdf_source )"
                )
            return {"type": "file_id", "file_id": file_id}

        raise ValueError(
            "error: pdf source.type must be 'url', 'base64', or 'file_id'. "
            "( runtime.engine -> _canonicalize_pdf_source )"
        )

    def _parse_data_url(self, value: str) -> tuple[str, str] | None:
        if not isinstance(value, str) or not value.startswith("data:"):
            return None
        if ";base64," not in value:
            return None
        header, data = value.split(";base64,", 1)
        media_type = header[5:].strip()
        if not media_type or not data:
            return None
        return media_type, data

    def _validate_modalities_against_capabilities(self, canonical_messages: list[dict[str, Any]]) -> None:
        raw_modalities = self._model_capability("input_modalities", ["text"])
        if not isinstance(raw_modalities, list):
            raw_modalities = ["text"]
        allowed_modalities = {m for m in raw_modalities if isinstance(m, str)}
        if not allowed_modalities:
            allowed_modalities = {"text"}

        raw_source_types = self._model_capability("input_source_types", {})
        source_type_map = raw_source_types if isinstance(raw_source_types, dict) else {}

        for message in canonical_messages:
            if not isinstance(message, dict) or "role" not in message:
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                if "text" not in allowed_modalities:
                    raise ValueError(
                        "error: model does not support text input. "
                        "( runtime.engine -> _validate_modalities_against_capabilities )"
                    )
                continue
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                modality = block.get("type")
                if modality not in ("text", "image", "pdf"):
                    continue
                if modality not in allowed_modalities:
                    raise ValueError(
                        f"error: model '{self.model}' does not support input modality '{modality}'. "
                        "( runtime.engine -> _validate_modalities_against_capabilities )"
                    )

                if modality == "text":
                    continue

                source = block.get("source")
                if not isinstance(source, dict):
                    raise ValueError(
                        f"error: {modality} block must include object field 'source'. "
                        "( runtime.engine -> _validate_modalities_against_capabilities )"
                    )
                source_type = source.get("type")
                # file_id is an OpenAI-specific upload reference; skip capability-map checks.
                if source_type == "file_id":
                    continue
                allowed_types_raw = source_type_map.get(modality, ["url", "base64"])
                if isinstance(allowed_types_raw, list) and allowed_types_raw:
                    allowed_types = {t for t in allowed_types_raw if isinstance(t, str)}
                else:
                    allowed_types = {"url", "base64"}
                if source_type not in allowed_types:
                    raise ValueError(
                        f"error: model '{self.model}' does not support source.type '{source_type}' "
                        f"for modality '{modality}'. ( runtime.engine -> _validate_modalities_against_capabilities )"
                    )

    def _openai_resolve_file(self, data: str, media_type: str, filename: str) -> str:
        return resolve_openai_file_upload(
            data=data,
            media_type=media_type,
            filename=filename,
            api_key=self.api_key,
            file_id_cache=self._file_id_cache,
            file_id_reverse=self._file_id_reverse,
        )

    def _project_canonical_to_openai(self, canonical_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for message in canonical_messages:
            if not isinstance(message, dict) or "role" not in message:
                projected.append(copy.deepcopy(message))
                continue

            out_message = {k: copy.deepcopy(v) for k, v in message.items() if k != "content"}
            content = message.get("content", "")
            if isinstance(content, str):
                out_message["content"] = content
                projected.append(out_message)
                continue

            if not isinstance(content, list):
                out_message["content"] = copy.deepcopy(content)
                projected.append(out_message)
                continue

            out_blocks: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")

                if block_type == "text":
                    text = block.get("text", "")
                    out_blocks.append({
                        "type": "input_text",
                        "text": text if isinstance(text, str) else str(text),
                    })
                    continue

                if block_type == "image":
                    source = block.get("source", {})
                    if not isinstance(source, dict):
                        raise ValueError(
                            "error: image block requires object field 'source'. "
                            "( runtime.engine -> _project_canonical_to_openai )"
                        )
                    source_type = source.get("type")
                    if source_type == "url":
                        out_blocks.append({
                            "type": "input_image",
                            "image_url": source.get("url", ""),
                        })
                        continue
                    if source_type == "base64":
                        media_type = source.get("media_type", "")
                        data = source.get("data", "")
                        out_blocks.append({
                            "type": "input_image",
                            "image_url": f"data:{media_type};base64,{data}",
                        })
                        continue
                    raise ValueError(
                        "error: image source.type must be 'url' or 'base64'. "
                        "( runtime.engine -> _project_canonical_to_openai )"
                    )

                if block_type == "pdf":
                    source = block.get("source", {})
                    if not isinstance(source, dict):
                        raise ValueError(
                            "error: pdf block requires object field 'source'. "
                            "( runtime.engine -> _project_canonical_to_openai )"
                        )
                    source_type = source.get("type")
                    if source_type == "url":
                        out_blocks.append({
                            "type": "input_file",
                            "file_url": source.get("url", ""),
                        })
                        continue
                    if source_type == "base64":
                        filename = source.get("filename", "document.pdf")
                        if not isinstance(filename, str) or not filename.strip():
                            filename = "document.pdf"
                        data = source.get("data", "")
                        if self.api_key and data:
                            # Upload to OpenAI Files API and cache the file_id so
                            # repeat run() calls on the same Broth instance don't
                            # re-upload the same file on every iteration.
                            file_id = self._openai_resolve_file(data, "application/pdf", filename)
                            out_blocks.append({
                                "type": "input_file",
                                "file_id": file_id,
                            })
                        else:
                            # Fallback when api_key unavailable: send inline data URL.
                            file_data = data
                            if isinstance(file_data, str) and file_data and not file_data.startswith("data:"):
                                file_data = f"data:application/pdf;base64,{file_data}"
                            out_blocks.append({
                                "type": "input_file",
                                "file_data": file_data,
                                "filename": filename,
                            })
                        continue
                    if source_type == "file_id":
                        out_blocks.append({
                            "type": "input_file",
                            "file_id": source.get("file_id", ""),
                        })
                        continue
                    raise ValueError(
                        "error: pdf source.type must be 'url', 'base64', or 'file_id'. "
                        "( runtime.engine -> _project_canonical_to_openai )"
                    )

                out_blocks.append(copy.deepcopy(block))

            out_message["content"] = out_blocks
            projected.append(out_message)
        return projected

    def _project_canonical_to_anthropic(self, canonical_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for message in canonical_messages:
            if not isinstance(message, dict) or "role" not in message:
                projected.append(copy.deepcopy(message))
                continue

            out_message = {k: copy.deepcopy(v) for k, v in message.items() if k != "content"}
            content = message.get("content", "")
            if isinstance(content, str):
                out_message["content"] = content
                projected.append(out_message)
                continue

            if not isinstance(content, list):
                out_message["content"] = copy.deepcopy(content)
                projected.append(out_message)
                continue

            out_blocks: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text", "")
                    out_blocks.append({
                        "type": "text",
                        "text": text if isinstance(text, str) else str(text),
                    })
                    continue

                if block_type == "image":
                    source = block.get("source", {})
                    if not isinstance(source, dict):
                        raise ValueError(
                            "error: image block requires object field 'source'. "
                            "( runtime.engine -> _project_canonical_to_anthropic )"
                        )
                    source_type = source.get("type")
                    if source_type == "url":
                        out_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": source.get("url", ""),
                            },
                        })
                        continue
                    if source_type == "base64":
                        img_data = source.get("data", "")
                        out_block: dict[str, Any] = {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": source.get("media_type", ""),
                                "data": img_data,
                            },
                        }
                        # Inject prompt-caching hint for large payloads.
                        if len(img_data) > 10_000:
                            out_block["cache_control"] = {"type": "ephemeral"}
                        out_blocks.append(out_block)
                        continue
                    raise ValueError(
                        "error: image source.type must be 'url' or 'base64'. "
                        "( runtime.engine -> _project_canonical_to_anthropic )"
                    )

                if block_type == "pdf":
                    source = block.get("source", {})
                    if not isinstance(source, dict):
                        raise ValueError(
                            "error: pdf block requires object field 'source'. "
                            "( runtime.engine -> _project_canonical_to_anthropic )"
                        )
                    source_type = source.get("type")
                    if source_type == "url":
                        out_blocks.append({
                            "type": "document",
                            "source": {
                                "type": "url",
                                "url": source.get("url", ""),
                            },
                        })
                        continue
                    if source_type == "base64":
                        pdf_data = source.get("data", "")
                        out_block = {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_data,
                            },
                        }
                        # Inject prompt-caching hint for large payloads.
                        if len(pdf_data) > 10_000:
                            out_block["cache_control"] = {"type": "ephemeral"}
                        out_blocks.append(out_block)
                        continue
                    if source_type == "file_id":
                        raise ValueError(
                            "error: pdf source.type 'file_id' is OpenAI-specific and not supported by Anthropic. "
                            "( runtime.engine -> _project_canonical_to_anthropic )"
                        )
                    raise ValueError(
                        "error: pdf source.type must be 'url', 'base64', or 'file_id'. "
                        "( runtime.engine -> _project_canonical_to_anthropic )"
                    )

                out_blocks.append(copy.deepcopy(block))

            out_message["content"] = out_blocks
            projected.append(out_message)
        return projected

    def _project_canonical_to_ollama(self, canonical_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for message in canonical_messages:
            if not isinstance(message, dict) or "role" not in message:
                projected.append(copy.deepcopy(message))
                continue

            out_message = {k: copy.deepcopy(v) for k, v in message.items() if k != "content"}
            content = message.get("content", "")
            if isinstance(content, str):
                out_message["content"] = content
                projected.append(out_message)
                continue

            if not isinstance(content, list):
                out_message["content"] = str(content)
                projected.append(out_message)
                continue

            text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text", "")
                    text_parts.append(text if isinstance(text, str) else str(text))
                    continue
                if block_type in ("image", "pdf"):
                    raise ValueError(
                        f"error: provider '{self.provider}' does not support modality '{block_type}'. "
                        "( runtime.engine -> _project_canonical_to_ollama )"
                    )
                raise ValueError(
                    "error: ollama content blocks only support text. "
                    "( runtime.engine -> _project_canonical_to_ollama )"
                )
            out_message["content"] = "".join(text_parts)
            projected.append(out_message)
        return projected

    def _project_canonical_to_gemini(self, canonical_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert canonical messages to Gemini ``contents`` format.

        Gemini uses ``role: "model"`` instead of ``"assistant"`` and supports
        ``parts`` lists containing text, inline_data (images/PDFs), function_call,
        and function_response items.
        """
        projected: list[dict[str, Any]] = []
        for message in canonical_messages:
            if not isinstance(message, dict) or "role" not in message:
                projected.append(copy.deepcopy(message))
                continue

            role = message.get("role", "")
            # System messages are handled separately (system_instruction) — skip.
            if role == "system":
                continue
            # Map "assistant" -> "model"
            if role == "assistant":
                role = "model"

            content = message.get("content", "")
            if isinstance(content, str):
                projected.append({"role": role, "parts": [{"text": content}]})
                continue

            if not isinstance(content, list):
                projected.append({"role": role, "parts": [{"text": str(content)}]})
                continue

            parts: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")

                if block_type == "text":
                    text = block.get("text", "")
                    parts.append({"text": text if isinstance(text, str) else str(text)})
                    continue

                if block_type == "image":
                    source = block.get("source", {})
                    if not isinstance(source, dict):
                        raise ValueError(
                            "error: image block requires object field 'source'. "
                            "( runtime.engine -> _project_canonical_to_gemini )"
                        )
                    source_type = source.get("type")
                    if source_type == "url":
                        url = source.get("url", "")
                        # Gemini supports file_data for URIs
                        parts.append({
                            "file_data": {
                                "file_uri": url,
                                "mime_type": source.get("media_type", "image/jpeg"),
                            },
                        })
                        continue
                    if source_type == "base64":
                        parts.append({
                            "inline_data": {
                                "mime_type": source.get("media_type", "image/jpeg"),
                                "data": source.get("data", ""),
                            },
                        })
                        continue
                    raise ValueError(
                        "error: image source.type must be 'url' or 'base64'. "
                        "( runtime.engine -> _project_canonical_to_gemini )"
                    )

                if block_type == "pdf":
                    source = block.get("source", {})
                    if not isinstance(source, dict):
                        raise ValueError(
                            "error: pdf block requires object field 'source'. "
                            "( runtime.engine -> _project_canonical_to_gemini )"
                        )
                    source_type = source.get("type")
                    if source_type == "url":
                        parts.append({
                            "file_data": {
                                "file_uri": source.get("url", ""),
                                "mime_type": "application/pdf",
                            },
                        })
                        continue
                    if source_type == "base64":
                        parts.append({
                            "inline_data": {
                                "mime_type": "application/pdf",
                                "data": source.get("data", ""),
                            },
                        })
                        continue
                    if source_type == "file_id":
                        raise ValueError(
                            "error: pdf source.type 'file_id' is OpenAI-specific and not supported by Gemini. "
                            "( runtime.engine -> _project_canonical_to_gemini )"
                        )
                    raise ValueError(
                        "error: pdf source.type must be 'url' or 'base64'. "
                        "( runtime.engine -> _project_canonical_to_gemini )"
                    )

                # Gemini tool_use / tool_result blocks from prior turns
                if block_type == "tool_use":
                    parts.append({
                        "function_call": {
                            "name": block.get("name", ""),
                            "args": block.get("input", {}),
                        },
                    })
                    continue

                if block_type == "tool_result":
                    raw_content = block.get("content", "")
                    try:
                        response_val = json.loads(raw_content) if isinstance(raw_content, str) and raw_content.strip() else {"result": raw_content}
                    except (json.JSONDecodeError, ValueError):
                        response_val = {"result": raw_content}
                    parts.append({
                        "function_response": {
                            "name": block.get("name", "unknown"),
                            "response": response_val if isinstance(response_val, dict) else {"result": response_val},
                        },
                    })
                    continue

                parts.append(copy.deepcopy(block))

            if parts:
                projected.append({"role": role, "parts": parts})
        return projected

    def _project_canonical_messages_for_provider(
        self,
        canonical_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self.provider == "openai":
            return self._project_canonical_to_openai(canonical_messages)
        if self.provider == "anthropic":
            return self._project_canonical_to_anthropic(canonical_messages)
        if self.provider == "ollama":
            return self._project_canonical_to_ollama(canonical_messages)
        if self.provider == "gemini":
            return self._project_canonical_to_gemini(canonical_messages)
        return copy.deepcopy(canonical_messages)

    def _run_loop(
        self,
        *,
        conversation: list[dict[str, Any]],
        payload: dict[str, Any],
        response_format_obj: ResponseFormat | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        max_loops: int,
        next_previous_response_id: str | None,
        next_openai_input: list[dict[str, Any]],
        use_openai_previous_response_chain: bool,
        on_tool_confirm: Callable | None,
        on_continuation_request: Callable | None,
        session_id: str | None,
        memory_namespace: str | None,
        run_id: str,
        iteration: int = 0,
        total_consumed_tokens: int = 0,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        last_turn_tokens: int = 0,
        last_turn_input_tokens: int = 0,
        last_turn_output_tokens: int = 0,
        toolkit_catalog_runtime: ToolkitCatalogRuntime | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        catalog_suspended = False
        try:
            while True:
                visible_toolkits = build_visible_toolkits(
                    eager_toolkits=list(self.toolkits),
                    catalog_runtime=toolkit_catalog_runtime,
                )
                runtime_toolkit = self._build_runtime_toolkit(toolkits=visible_toolkits)
                self._validate_runtime_toolkit_support(runtime_toolkit)

                if iteration >= max_loops:
                    if on_continuation_request is not None:
                        resp = on_continuation_request({"iteration": max_loops})
                        if resp and resp.get("approved"):
                            extra = max(1, int(resp.get("extra_iterations", 10)))
                            max_loops += extra
                        else:
                            break
                    else:
                        break

                self._emit(callback, "iteration_started", run_id, iteration=iteration)
                request_messages = conversation
                request_previous_response_id = None
                if self.provider == "openai" and use_openai_previous_response_chain:
                    request_messages = next_openai_input
                    request_previous_response_id = next_previous_response_id
                    if any(isinstance(msg, dict) and msg.get("role") is not None for msg in request_messages):
                        request_messages = self._inject_workspace_pin_messages(
                            messages=request_messages,
                            session_id=session_id,
                        )
                else:
                    request_messages = self._inject_workspace_pin_messages(
                        messages=request_messages,
                        session_id=session_id,
                    )

                try:
                    turn = self._fetch_once(
                        messages=request_messages,
                        payload=payload,
                        response_format=response_format_obj,
                        callback=callback,
                        verbose=verbose,
                        run_id=run_id,
                        iteration=iteration,
                        toolkit=runtime_toolkit,
                        emit_stream=True,
                        previous_response_id=request_previous_response_id,
                    )
                except providers.openai.BadRequestError as exc:
                    should_fallback = (
                        self.provider == "openai"
                        and use_openai_previous_response_chain
                        and bool(request_previous_response_id)
                        and self._is_previous_response_not_found_error(exc)
                    )
                    if not should_fallback:
                        raise

                    self._emit(
                        callback,
                        "previous_response_fallback",
                        run_id,
                        iteration=iteration,
                        reason="previous_response_not_found",
                        previous_response_id=request_previous_response_id,
                    )
                    use_openai_previous_response_chain = False
                    next_previous_response_id = None

                    output_call_ids = {
                        m.get("call_id")
                        for m in request_messages
                        if isinstance(m, dict) and m.get("type") == "function_call_output"
                    }
                    if output_call_ids:
                        systems = [
                            m for m in conversation
                            if isinstance(m, dict) and m.get("role") == "system"
                        ]
                        user_msgs = [
                            m for m in conversation
                            if isinstance(m, dict) and m.get("role") == "user"
                        ]
                        last_user = [user_msgs[-1]] if user_msgs else []
                        matched_calls = [
                            m for m in conversation
                            if isinstance(m, dict)
                            and m.get("type") == "function_call"
                            and m.get("call_id") in output_call_ids
                        ]
                        fallback_messages = systems + last_user + matched_calls + list(request_messages)
                    else:
                        fallback_messages = conversation

                    turn = self._fetch_once(
                        messages=fallback_messages,
                        payload=payload,
                        response_format=response_format_obj,
                        callback=callback,
                        verbose=verbose,
                        run_id=run_id,
                        iteration=iteration,
                        toolkit=runtime_toolkit,
                        emit_stream=True,
                        previous_response_id=None,
                    )

                if self.provider == "openai":
                    next_previous_response_id = (
                        turn.response_id if use_openai_previous_response_chain else None
                    )
                    self.last_response_id = turn.response_id
                turn_usage = self._normalize_token_usage(
                    consumed_tokens=turn.consumed_tokens,
                    input_tokens=turn.input_tokens,
                    output_tokens=turn.output_tokens,
                )
                total_consumed_tokens += turn_usage.consumed_tokens
                total_input_tokens += turn_usage.input_tokens
                total_output_tokens += turn_usage.output_tokens
                last_turn_tokens = turn_usage.consumed_tokens
                last_turn_input_tokens = turn_usage.input_tokens
                last_turn_output_tokens = turn_usage.output_tokens

                if turn.reasoning_items:
                    self.last_reasoning_items = copy.deepcopy(turn.reasoning_items)
                    self._emit(
                        callback,
                        "reasoning",
                        run_id,
                        iteration=iteration,
                        response_id=turn.response_id,
                        reasoning_items=turn.reasoning_items,
                    )

                conversation.extend(turn.assistant_messages)
                self._emit(
                    callback,
                    "response_received",
                    run_id,
                    iteration=iteration,
                    response_id=turn.response_id,
                    has_tool_calls=bool(turn.tool_calls),
                    bundle=copy.deepcopy(
                        self._build_bundle(
                            total_consumed_tokens,
                            last_turn_tokens,
                            run_input_tokens=total_input_tokens,
                            run_output_tokens=total_output_tokens,
                            status="running" if turn.tool_calls else "completed",
                        )
                    )
                )

                if turn.tool_calls:
                    execution = self._coerce_tool_execution_outcome(
                        self._execute_tool_calls(
                            tool_calls=turn.tool_calls,
                            run_id=run_id,
                            iteration=iteration,
                            callback=callback,
                            on_tool_confirm=on_tool_confirm,
                            session_id=session_id,
                            toolkits=visible_toolkits,
                        )
                    )

                    if execution.awaiting_human_input and execution.human_input_request is not None:
                        catalog_payload = None
                        if toolkit_catalog_runtime is not None:
                            catalog_payload = self._stash_suspended_toolkit_catalog_runtime(toolkit_catalog_runtime)
                            catalog_suspended = True
                        continuation = self._build_human_input_continuation(
                            request=execution.human_input_request,
                            payload=payload,
                            response_format_obj=response_format_obj,
                            iteration=iteration + 1,
                            max_iterations=max_loops,
                            previous_response_id=next_previous_response_id,
                            use_openai_previous_response_chain=use_openai_previous_response_chain,
                            session_id=session_id,
                            memory_namespace=memory_namespace,
                            consumed_tokens=total_consumed_tokens,
                            input_tokens=total_input_tokens,
                            output_tokens=total_output_tokens,
                            last_turn_tokens=last_turn_tokens,
                            last_turn_input_tokens=last_turn_input_tokens,
                            last_turn_output_tokens=last_turn_output_tokens,
                            toolkit_catalog=catalog_payload,
                        )
                        bundle = self._build_bundle(
                            total_consumed_tokens,
                            last_turn_tokens,
                            run_input_tokens=total_input_tokens,
                            run_output_tokens=total_output_tokens,
                            status="awaiting_human_input",
                            human_input_request=execution.human_input_request.to_dict(),
                            continuation=continuation,
                        )
                        self.last_consumed_tokens = total_consumed_tokens
                        self.consumed_tokens += total_consumed_tokens
                        return conversation, bundle

                    tool_messages = execution.result_messages
                    if execution.should_observe and tool_messages:
                        observation, observe_usage = self._observe_tool_batch(
                            full_messages=conversation,
                            tool_messages=tool_messages,
                            payload=payload,
                        )
                        total_consumed_tokens += observe_usage.consumed_tokens
                        total_input_tokens += observe_usage.input_tokens
                        total_output_tokens += observe_usage.output_tokens
                        if observation:
                            self._inject_observation(tool_messages[-1], observation)
                            self._emit(
                                callback,
                                "observation",
                                run_id,
                                iteration=iteration,
                                content=observation,
                            )

                    conversation.extend(tool_messages)
                    if self.provider == "openai" and use_openai_previous_response_chain:
                        if next_previous_response_id:
                            next_openai_input = copy.deepcopy(tool_messages)
                        else:
                            next_openai_input = copy.deepcopy(conversation)
                    self._emit(callback, "iteration_completed", run_id, iteration=iteration, has_tool_calls=True)
                    iteration += 1
                    continue

                self._apply_response_format(conversation, response_format_obj)
                final_text = self._last_assistant_text(conversation)
                bundle = self._build_bundle(
                    total_consumed_tokens,
                    last_turn_tokens,
                    run_input_tokens=total_input_tokens,
                    run_output_tokens=total_output_tokens,
                    status="completed",
                )
                self._emit(
                    callback,
                    "final_message",
                    run_id,
                    iteration=iteration,
                    content=final_text,
                )
                self._emit(
                    callback,
                    "run_completed",
                    run_id,
                    iteration=iteration,
                    bundle=copy.deepcopy(bundle),
                )
                self.last_consumed_tokens = total_consumed_tokens
                self.consumed_tokens += total_consumed_tokens
                self._finalize_memory_commit(
                    callback=callback,
                    run_id=run_id,
                    iteration=iteration,
                    conversation=conversation,
                    session_id=session_id,
                    memory_namespace=memory_namespace,
                )
                return conversation, bundle

            self._emit(callback, "run_max_iterations", run_id, iteration=max_loops)
            self.last_consumed_tokens = total_consumed_tokens
            self.consumed_tokens += total_consumed_tokens
            self._finalize_memory_commit(
                callback=callback,
                run_id=run_id,
                iteration=max_loops,
                conversation=conversation,
                session_id=session_id,
                memory_namespace=memory_namespace,
            )
            bundle = self._build_bundle(
                total_consumed_tokens,
                last_turn_tokens,
                run_input_tokens=total_input_tokens,
                run_output_tokens=total_output_tokens,
                status="max_iterations",
            )
            return conversation, bundle
        finally:
            if toolkit_catalog_runtime is not None and not catalog_suspended:
                toolkit_catalog_runtime.shutdown()

    def run(
        self,
        messages,
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
    ):
        run_id = str(uuid.uuid4())
        raw_seed_messages = copy.deepcopy(list(messages or []))
        canonical_seed_messages = self._canonicalize_seed_messages(raw_seed_messages)
        self._validate_modalities_against_capabilities(canonical_seed_messages)
        self._last_canonical_seed = canonical_seed_messages
        seed_messages = self._project_canonical_messages_for_provider(canonical_seed_messages)
        if self.memory_manager is not None and session_id:
            self.memory_manager.ensure_long_term_components(broth_instance=self)
            try:
                seed_messages = self._prepare_memory_messages(
                    session_id=session_id,
                    incoming=seed_messages,
                    max_context_window_tokens=self.max_context_window_tokens,
                    model=self.model,
                    summary_generator=self._generate_memory_summary,
                    memory_namespace=memory_namespace,
                )
                memory_prepare_info = {
                    **self.memory_manager.last_prepare_info,
                    "applied": True,
                }
                self._emit(
                    callback,
                    "memory_prepare",
                    run_id,
                    iteration=0,
                    **memory_prepare_info,
                )
            except Exception as exc:
                self._emit(
                    callback,
                    "memory_prepare",
                    run_id,
                    iteration=0,
                    session_id=session_id,
                    applied=False,
                    fallback_reason=f"memory_prepare_failed: {exc}",
                )

        payload = dict(payload or {})
        effective_payload = self._merged_payload(payload)
        max_loops = max_iterations or self.max_iterations
        toolkit_catalog_runtime = self._build_toolkit_catalog_runtime()
        supports_prev = bool(self._model_capability("supports_previous_response_id", False))
        store_enabled = effective_payload.get("store") is not False
        use_openai_previous_response_chain = (
            self.provider == "openai" and supports_prev and store_enabled
        )
        next_previous_response_id = (
            previous_response_id if use_openai_previous_response_chain else None
        )
        conversation = copy.deepcopy(seed_messages)
        next_openai_input = copy.deepcopy(seed_messages)
        self.last_reasoning_items = []
        effective_on_tool_confirm = on_tool_confirm or self.on_tool_confirm

        self._emit(callback, "run_started", run_id, iteration=0, provider=self.provider, model=self.model)
        return self._run_loop(
            conversation=conversation,
            payload=payload,
            response_format_obj=response_format,
            callback=callback,
            verbose=verbose,
            max_loops=max_loops,
            next_previous_response_id=next_previous_response_id,
            next_openai_input=next_openai_input,
            use_openai_previous_response_chain=use_openai_previous_response_chain,
            on_tool_confirm=effective_on_tool_confirm,
            on_continuation_request=on_continuation_request,
            session_id=session_id,
            memory_namespace=memory_namespace,
            run_id=run_id,
            toolkit_catalog_runtime=toolkit_catalog_runtime,
        )

    def resume_human_input(
        self,
        *,
        conversation: list[dict[str, Any]],
        continuation: dict[str, Any],
        response: HumanInputResponse | dict[str, Any],
        payload: dict[str, Any] | None = None,
        response_format: ResponseFormat | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
        verbose: bool = False,
        on_tool_confirm: Callable | None = None,
        on_continuation_request: Callable | None = None,
        session_id: str | None = None,
        memory_namespace: str | None = None,
    ):
        if not isinstance(conversation, list):
            raise TypeError("conversation must be a list of provider-projected messages")
        if not isinstance(continuation, dict):
            raise TypeError("continuation must be a dict returned by bundle['continuation']")
        if continuation.get("type") != "human_input_continuation":
            raise ValueError("continuation must be a human_input_continuation bundle")

        pending_request = continuation.get("request")
        if not isinstance(pending_request, dict):
            raise ValueError("continuation is missing the pending human input request")
        request = HumanInputRequest.from_dict(pending_request)

        continuation_provider = continuation.get("provider")
        if isinstance(continuation_provider, str) and continuation_provider != self.provider:
            raise ValueError("continuation provider does not match this broth instance")

        expected_session_id = continuation.get("session_id")
        effective_session_id = session_id if session_id is not None else expected_session_id
        if (
            isinstance(expected_session_id, str)
            and session_id is not None
            and session_id != expected_session_id
        ):
            raise ValueError("resume_human_input requires the same session_id as the suspended run")

        effective_memory_namespace = (
            memory_namespace
            if memory_namespace is not None
            else continuation.get("memory_namespace")
        )

        effective_payload = (
            dict(payload)
            if payload is not None
            else copy.deepcopy(continuation.get("payload") or {})
        )
        effective_response_format = (
            response_format
            if response_format is not None
            else self._deserialize_response_format(continuation.get("response_format"))
        )
        toolkit_catalog_runtime = self._restore_toolkit_catalog_runtime(continuation)
        human_response = HumanInputResponse.from_raw(response, request=request)
        tool_call = ToolCall(
            call_id=str(continuation.get("call_id") or request.request_id),
            name=ASK_USER_QUESTION_TOOL_NAME,
            arguments={},
        )
        tool_message = self._build_tool_message(
            tool_call=tool_call,
            tool_result=human_response.to_tool_result(),
        )
        resumed_conversation = copy.deepcopy(conversation)
        resumed_conversation.append(tool_message)

        continuation_previous_response_id = continuation.get("previous_response_id")
        next_previous_response_id = (
            continuation_previous_response_id
            if isinstance(continuation_previous_response_id, str)
            else None
        )
        use_openai_previous_response_chain = bool(
            continuation.get("use_openai_previous_response_chain", False)
        )
        if self.provider == "openai" and use_openai_previous_response_chain and next_previous_response_id:
            next_openai_input = [tool_message]
        else:
            next_openai_input = copy.deepcopy(resumed_conversation)

        if self.memory_manager is not None and effective_session_id:
            self.memory_manager.ensure_long_term_components(broth_instance=self)

        run_id = str(uuid.uuid4())
        self.last_reasoning_items = []
        effective_on_tool_confirm = on_tool_confirm or self.on_tool_confirm
        start_iteration = int(continuation.get("iteration") or 0)
        max_loops = int(continuation.get("max_iterations") or self.max_iterations)
        total_consumed_tokens = int(continuation.get("consumed_tokens") or 0)
        total_input_tokens = int(continuation.get("input_tokens") or 0)
        total_output_tokens = int(continuation.get("output_tokens") or 0)
        last_turn_tokens = int(continuation.get("last_turn_tokens") or 0)
        last_turn_input_tokens = int(continuation.get("last_turn_input_tokens") or 0)
        last_turn_output_tokens = int(continuation.get("last_turn_output_tokens") or 0)

        self._emit(callback, "run_started", run_id, iteration=start_iteration, provider=self.provider, model=self.model)
        return self._run_loop(
            conversation=resumed_conversation,
            payload=effective_payload,
            response_format_obj=effective_response_format,
            callback=callback,
            verbose=verbose,
            max_loops=max_loops,
            next_previous_response_id=next_previous_response_id,
            next_openai_input=next_openai_input,
            use_openai_previous_response_chain=use_openai_previous_response_chain,
            on_tool_confirm=effective_on_tool_confirm,
            on_continuation_request=on_continuation_request,
            session_id=effective_session_id,
            memory_namespace=effective_memory_namespace if isinstance(effective_memory_namespace, str) else None,
            run_id=run_id,
            iteration=start_iteration,
            total_consumed_tokens=total_consumed_tokens,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            last_turn_tokens=last_turn_tokens,
            last_turn_input_tokens=last_turn_input_tokens,
            last_turn_output_tokens=last_turn_output_tokens,
            toolkit_catalog_runtime=toolkit_catalog_runtime,
        )

    def _emit(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
        event_type: str,
        run_id: str,
        *,
        iteration: int,
        **extra: Any,
    ):
        if callback is None:
            return
        event = {
            "type": event_type,
            "run_id": run_id,
            "iteration": iteration,
            "timestamp": time.time(),
        }
        event.update(extra)
        callback(event)

    def _emit_request_messages(
        self,
        *,
        callback: Callable[[dict[str, Any]], None] | None,
        run_id: str,
        iteration: int,
        provider: str,
        messages: list[dict[str, Any]],
        previous_response_id: str | None = None,
        system: str | None = None,
        tool_names: list[str] | None = None,
    ) -> None:
        event_payload: dict[str, Any] = {
            "provider": provider,
            "messages": copy.deepcopy(messages),
        }
        if previous_response_id is not None:
            event_payload["previous_response_id"] = previous_response_id
        if system is not None:
            event_payload["system"] = system
        if tool_names:
            event_payload["tool_names"] = list(tool_names)
        self._emit(
            callback,
            "request_messages",
            run_id,
            iteration=iteration,
            **event_payload,
        )

    @staticmethod
    def _tool_names_for_trace(tools_json: list[dict[str, Any]] | None) -> list[str]:
        tool_names: list[str] = []
        for tool in tools_json or []:
            name = str(tool.get("name", "")).strip()
            if name:
                tool_names.append(name)
        return tool_names

    def _is_previous_response_not_found_error(self, exc: Exception) -> bool:
        """Return True when OpenAI reports an invalid previous_response_id.

        Also catches the case where a model doesn't persist the previous response
        and returns 'No tool call found for function call output' — which means the
        function_call items from the prior response are inaccessible, so we must
        fall back to sending the full conversation.
        """
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error_obj = body.get("error")
            if isinstance(error_obj, dict):
                code = error_obj.get("code")
                if code == "previous_response_not_found":
                    return True

                param = error_obj.get("param")
                message = str(error_obj.get("message", ""))
                if param == "previous_response_id" and "not found" in message.lower():
                    return True

                # gpt-4.5 and some preview models don't persist the previous
                # response's tool calls, so tool outputs have no matching call.
                if "no tool call found for function call output" in message.lower():
                    return True

        text = str(exc).lower()
        if "previous_response_id" in text and "not found" in text:
            return True
        if "no tool call found for function call output" in text:
            return True
        return False

    def _normalize_openai_input_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize OpenAI response items so they can be reused as input safely."""
        normalized: list[dict[str, Any]] = []

        for message in messages:
            if not isinstance(message, dict):
                normalized.append(copy.deepcopy(message))
                continue

            item_type = message.get("type")
            if item_type == "function_call":
                call_id = message.get("call_id") or message.get("id") or str(uuid.uuid4())
                normalized.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": message.get("name", ""),
                    "arguments": message.get("arguments", "{}"),
                })
                continue

            normalized.append(copy.deepcopy(message))

        return normalized

    def _fetch_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any],
        response_format: ResponseFormat | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        run_id: str,
        iteration: int,
        toolkit: BaseToolkit,
        emit_stream: bool,
        previous_response_id: str | None = None,
        openai_text_format: dict[str, Any] | None = None,
    ) -> ProviderTurnResult:
        if self.provider == "openai":
            return self._openai_fetch_once(
                messages=messages,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                iteration=iteration,
                toolkit=toolkit,
                emit_stream=emit_stream,
                previous_response_id=previous_response_id,
                openai_text_format=openai_text_format,
            )
        if self.provider == "ollama":
            return self._ollama_fetch_once(
                messages=messages,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                iteration=iteration,
                toolkit=toolkit,
                emit_stream=emit_stream,
            )
        if self.provider == "anthropic":
            return self._anthropic_fetch_once(
                messages=messages,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                iteration=iteration,
                toolkit=toolkit,
                emit_stream=emit_stream,
            )
        if self.provider == "gemini":
            return self._gemini_fetch_once(
                messages=messages,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                iteration=iteration,
                toolkit=toolkit,
                emit_stream=emit_stream,
            )
        raise ValueError("error: unsupported provider specified. ( runtime.engine -> run )")

    def _merged_payload(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        resolved_key = self._resolve_model_key(self.default_payload)
        defaults = copy.deepcopy(self.default_payload.get(resolved_key, {}) if resolved_key else {})
        if not isinstance(defaults, dict):
            return {}

        user_payload = payload or {}
        for key in list(defaults.keys()):
            if key in user_payload:
                defaults[key] = user_payload[key]

        allowed_keys = self._model_capability("allowed_payload_keys", None)
        if isinstance(allowed_keys, list) and allowed_keys:
            allowed_key_set = {key for key in allowed_keys if isinstance(key, str)}
            # Also inject user-supplied keys that are allowed but absent from defaults
            # (e.g. tool_choice, which has no default value for reasoning models)
            for key in user_payload:
                if key in allowed_key_set and key not in defaults:
                    defaults[key] = user_payload[key]
            defaults = {key: value for key, value in defaults.items() if key in allowed_key_set}

        if self.provider == "anthropic" and "temperature" in defaults and "top_p" in defaults:
            user_set_temperature = "temperature" in user_payload
            user_set_top_p = "top_p" in user_payload
            if user_set_top_p and not user_set_temperature:
                defaults.pop("temperature", None)
            else:
                defaults.pop("top_p", None)

        # Strip sentinel None values (e.g. "tool_choice": null in model defaults)
        # so they are not forwarded to the provider API unless the user explicitly set them.
        defaults = {k: v for k, v in defaults.items() if v is not None or k in user_payload}

        return defaults

    def _openai_fetch_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any],
        response_format: ResponseFormat | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        run_id: str,
        iteration: int,
        toolkit: BaseToolkit,
        emit_stream: bool,
        previous_response_id: str | None = None,
        openai_text_format: dict[str, Any] | None = None,
        _allow_retry: bool = True,
    ) -> ProviderTurnResult:
        if not self.api_key:
            raise ValueError("error: openai_api_key is required for openai provider")

        client_cls = providers.OpenAI
        openai_client = client_cls(api_key=self.api_key)
        normalized_messages = self._normalize_openai_input_messages(messages)
        request_payload = self._merged_payload(payload)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": normalized_messages,
            **request_payload,
            "stream": True,
        }
        if previous_response_id:
            request_kwargs["previous_response_id"] = previous_response_id

        tools_json = toolkit.to_json()
        supports_tools = bool(self._model_capability("supports_tools", True))
        if tools_json and supports_tools:
            request_kwargs["tools"] = tools_json

        resolved_text_format = None
        if isinstance(openai_text_format, dict):
            resolved_text_format = copy.deepcopy(openai_text_format)
        elif response_format is not None:
            resolved_text_format = response_format.to_openai()

        if isinstance(resolved_text_format, dict):
            text_config = (
                dict(request_kwargs["text"])
                if isinstance(request_kwargs.get("text"), dict)
                else {}
            )
            text_config["format"] = resolved_text_format
            request_kwargs["text"] = text_config

        self._emit_request_messages(
            callback=callback,
            run_id=run_id,
            iteration=iteration,
            provider="openai",
            messages=normalized_messages,
            previous_response_id=previous_response_id,
            tool_names=self._tool_names_for_trace(tools_json),
        )

        collected_chunks: list[str] = []
        completed_response = None
        created_response_id: str | None = None
        output_items_from_events: dict[int, dict[str, Any]] = {}

        try:
            with openai_client.responses.create(**request_kwargs) as stream_response:
                for chunk in stream_response:
                    chunk_type = getattr(chunk, "type", None)

                    if chunk_type == "response.output_text.delta":
                        delta = getattr(chunk, "delta", "") or ""
                        if delta:
                            collected_chunks.append(delta)
                            if verbose and clear_output is not None:
                                clear_output(wait=True)
                                print("".join(collected_chunks))
                            if emit_stream:
                                self._emit(
                                    callback,
                                    "token_delta",
                                    run_id,
                                    iteration=iteration,
                                    provider="openai",
                                    delta=delta,
                                    accumulated_text="".join(collected_chunks),
                                )
                    elif chunk_type == "response.error":
                        raise ValueError("error: LLM text generation failed. ( runtime.engine -> _openai_fetch_once )")
                    elif chunk_type == "response.created":
                        created = self._as_dict(getattr(chunk, "response", None))
                        if isinstance(created, dict):
                            cid = created.get("id")
                            if isinstance(cid, str) and cid:
                                created_response_id = cid
                    elif chunk_type == "response.output_item.done":
                        item = self._as_dict(getattr(chunk, "item", None))
                        output_index = getattr(chunk, "output_index", None)
                        if isinstance(item, dict) and isinstance(output_index, int):
                            output_items_from_events[output_index] = item
                    elif chunk_type == "response.completed":
                        completed_response = getattr(chunk, "response", None)
        except providers.openai.NotFoundError as exc:
            # A cached file_id was deleted/expired on the OpenAI side.  Evict the
            # stale entry, re-project the canonical seed messages (which will trigger
            # a fresh file upload via _openai_resolve_file), and retry once.
            if not _allow_retry or not self._last_canonical_seed:
                raise
            stale_ids = {v for v in self._file_id_reverse if v in str(exc)}
            if stale_ids:
                for fid in stale_ids:
                    sha = self._file_id_reverse.pop(fid, None)
                    if sha:
                        self._file_id_cache.pop(sha, None)
            else:
                # Can't identify which id failed — clear everything.
                self._file_id_cache.clear()
                self._file_id_reverse.clear()
            fresh_messages = self._project_canonical_messages_for_provider(self._last_canonical_seed)
            request_kwargs["input"] = fresh_messages
            return self._openai_fetch_once(
                messages=fresh_messages,
                payload=payload,
                response_format=response_format,
                callback=callback,
                verbose=verbose,
                run_id=run_id,
                iteration=iteration,
                toolkit=toolkit,
                emit_stream=emit_stream,
                previous_response_id=previous_response_id,
                openai_text_format=openai_text_format,
                _allow_retry=False,
            )

        if completed_response is None:
            if output_items_from_events:
                outputs = [
                    output_items_from_events[idx]
                    for idx in sorted(output_items_from_events.keys())
                ]
                response_id = created_response_id
                token_usage = TokenUsage()
            else:
                # gpt-5 / preview models occasionally close the stream without emitting
                # response.completed. If we collected text deltas, return them gracefully
                # rather than raising — the content is valid, just the envelope is missing.
                if collected_chunks:
                    full_text = "".join(collected_chunks).strip()
                    return ProviderTurnResult(
                        assistant_messages=[{"role": "assistant", "content": full_text}],
                        tool_calls=[],
                        final_text=full_text,
                        response_id=created_response_id,
                        consumed_tokens=0,
                        input_tokens=0,
                        output_tokens=0,
                    )
                raise ValueError("error: openai stream ended without completion payload")
        else:
            outputs = getattr(completed_response, "output", None) or []
            response_id = getattr(completed_response, "id", None)
            usage = getattr(completed_response, "usage", None)
            token_usage = self._extract_openai_token_usage(usage)

        assistant_messages: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        final_text_parts: list[str] = []
        reasoning_items: list[dict[str, Any]] = []

        for output_item in outputs:
            item = self._as_dict(output_item)
            item_type = item.get("type")

            if item_type == "function_call":
                call_id = item.get("call_id") or item.get("id") or str(uuid.uuid4())
                tool_calls.append(
                    ToolCall(
                        call_id=call_id,
                        name=item.get("name", ""),
                        arguments=item.get("arguments", "{}"),
                    )
                )
                assistant_messages.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "{}"),
                })
                continue

            if item_type == "message":
                text = self._extract_openai_message_text(item)
                if text:
                    assistant_messages.append({"role": "assistant", "content": text})
                    final_text_parts.append(text)
                continue

            if item_type == "reasoning":
                reasoning_items.append(item)
                continue

        if not tool_calls and not final_text_parts and collected_chunks:
            full_text = "".join(collected_chunks)
            assistant_messages.append({"role": "assistant", "content": full_text})
            final_text_parts.append(full_text)

        return ProviderTurnResult(
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            final_text="".join(final_text_parts).strip(),
            response_id=response_id,
            reasoning_items=reasoning_items or None,
            consumed_tokens=token_usage.consumed_tokens,
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
        )

    def _ollama_fetch_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any],
        response_format: ResponseFormat | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        run_id: str,
        iteration: int,
        toolkit: BaseToolkit,
        emit_stream: bool,
    ) -> ProviderTurnResult:
        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

        tools_json = toolkit.to_json()
        tools = []
        for tool in tools_json:
            if tool.get("type") == "function":
                fn = {k: v for k, v in tool.items() if k != "type"}
                tools.append({"type": "function", "function": fn})
            else:
                tools.append(tool)

        if tools:
            request_body["tools"] = tools
            request_body["tool_choice"] = "auto"

        merged_payload = self._merged_payload(payload)
        if merged_payload:
            request_body["options"] = merged_payload

        if response_format is not None:
            request_body["format"] = response_format.to_ollama()

        self._emit_request_messages(
            callback=callback,
            run_id=run_id,
            iteration=iteration,
            provider="ollama",
            messages=request_body.get("messages", []),
            tool_names=self._tool_names_for_trace(tools_json),
        )

        collected_chunks: list[str] = []
        latest_prompt_eval_count = 0
        latest_eval_count = 0

        httpx_module = providers.httpx
        with httpx_module.stream("POST", "http://localhost:11434/api/chat", json=request_body, timeout=None) as response:
            if response.status_code >= 400:
                detail = response.read().decode()
                raise ValueError(f"error: {detail} ( runtime.engine -> _ollama_fetch_once )")
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue

                data = json.loads(line)
                if data.get("error"):
                    raise ValueError(f"error: {data['error']} ( runtime.engine -> _ollama_fetch_once )")
                if isinstance(data.get("prompt_eval_count"), int):
                    latest_prompt_eval_count = data["prompt_eval_count"]
                if isinstance(data.get("eval_count"), int):
                    latest_eval_count = data["eval_count"]

                message = data.get("message") or {}
                delta = message.get("content", "") or message.get("thinking", "")
                if delta:
                    collected_chunks.append(delta)
                    if verbose and clear_output is not None:
                        clear_output(wait=True)
                        print("".join(collected_chunks))
                    if emit_stream:
                        self._emit(
                            callback,
                            "token_delta",
                            run_id,
                            iteration=iteration,
                            provider="ollama",
                            delta=delta,
                            accumulated_text="".join(collected_chunks),
                        )

                raw_tool_calls = message.get("tool_calls") or []
                if raw_tool_calls:
                    assistant_message = {
                        "role": "assistant",
                        "content": message.get("content", ""),
                        "tool_calls": raw_tool_calls,
                    }
                    tool_calls: list[ToolCall] = []
                    for raw_tool_call in raw_tool_calls:
                        fn = raw_tool_call.get("function", {}) or {}
                        tool_calls.append(
                            ToolCall(
                                call_id=raw_tool_call.get("id") or str(uuid.uuid4()),
                                name=fn.get("name", ""),
                                arguments=fn.get("arguments", {}),
                            )
                        )

                    return ProviderTurnResult(
                        assistant_messages=[assistant_message],
                        tool_calls=tool_calls,
                        final_text="",
                        consumed_tokens=latest_prompt_eval_count + latest_eval_count,
                        input_tokens=latest_prompt_eval_count,
                        output_tokens=latest_eval_count,
                    )

                if data.get("done", False):
                    full_message = message.get("content") or "".join(collected_chunks)
                    return ProviderTurnResult(
                        assistant_messages=[{"role": "assistant", "content": full_message}],
                        tool_calls=[],
                        final_text=full_message,
                        consumed_tokens=latest_prompt_eval_count + latest_eval_count,
                        input_tokens=latest_prompt_eval_count,
                        output_tokens=latest_eval_count,
                    )

        raise ValueError("error: unexpected termination of ollama stream. ( runtime.engine -> _ollama_fetch_once )")

    def _anthropic_fetch_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any],
        response_format: ResponseFormat | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        run_id: str,
        iteration: int,
        toolkit: BaseToolkit,
        emit_stream: bool,
    ) -> ProviderTurnResult:
        anthropic_client_cls = providers.Anthropic
        if anthropic_client_cls is None:
            raise ImportError("anthropic package is required for anthropic provider — pip install anthropic")
        if not self.api_key:
            raise ValueError("error: api_key is required for anthropic provider")

        client = anthropic_client_cls(api_key=self.api_key)
        request_payload = self._merged_payload(payload)

        # ── separate system prompt from messages ───────────────────────────
        system_prompt: str | None = None
        chat_messages: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                chat_messages.append(msg)

        # ── build tools for Anthropic format ───────────────────────────────
        tools_json = toolkit.to_json()
        anthropic_tools: list[dict[str, Any]] = []
        supports_tools = bool(self._model_capability("supports_tools", True))
        if tools_json and supports_tools:
            for t in tools_json:
                params = t.get("parameters", {})
                anthropic_tools.append({
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "input_schema": params,
                })

        # ── build request kwargs ───────────────────────────────────────────
        max_tokens = request_payload.pop("max_tokens", 4096)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
            **request_payload,
        }
        if system_prompt:
            request_kwargs["system"] = system_prompt
        if anthropic_tools:
            request_kwargs["tools"] = anthropic_tools

        self._emit_request_messages(
            callback=callback,
            run_id=run_id,
            iteration=iteration,
            provider="anthropic",
            messages=chat_messages,
            system=system_prompt if isinstance(system_prompt, str) else None,
            tool_names=self._tool_names_for_trace(tools_json),
        )

        # ── stream response ────────────────────────────────────────────────
        collected_chunks: list[str] = []
        assistant_messages: list[dict[str, Any]] = []
        tool_calls: list[ToolCall] = []
        final_text_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0

        # Track content blocks being built
        current_tool_name: str = ""
        current_tool_id: str = ""
        current_tool_json_parts: list[str] = []
        content_blocks: list[dict[str, Any]] = []

        with client.messages.stream(**request_kwargs) as stream:
            for event in stream:
                event_type = getattr(event, "type", None)

                if event_type == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if block is not None:
                        block_dict = self._as_dict(block)
                        if block_dict.get("type") == "tool_use":
                            current_tool_name = block_dict.get("name", "")
                            current_tool_id = block_dict.get("id", str(uuid.uuid4()))
                            current_tool_json_parts = []

                elif event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta is not None:
                        delta_dict = self._as_dict(delta)
                        delta_type = delta_dict.get("type", "")

                        if delta_type == "text_delta":
                            text = delta_dict.get("text", "")
                            if text:
                                collected_chunks.append(text)
                                if verbose and clear_output is not None:
                                    clear_output(wait=True)
                                    print("".join(collected_chunks))
                                if emit_stream:
                                    self._emit(
                                        callback,
                                        "token_delta",
                                        run_id,
                                        iteration=iteration,
                                        provider="anthropic",
                                        delta=text,
                                        accumulated_text="".join(collected_chunks),
                                    )

                        elif delta_type == "input_json_delta":
                            partial = delta_dict.get("partial_json", "")
                            if partial:
                                current_tool_json_parts.append(partial)

                elif event_type == "content_block_stop":
                    if current_tool_name:
                        raw_json = "".join(current_tool_json_parts)
                        try:
                            arguments = json.loads(raw_json) if raw_json.strip() else {}
                        except json.JSONDecodeError:
                            arguments = raw_json

                        tool_calls.append(ToolCall(
                            call_id=current_tool_id,
                            name=current_tool_name,
                            arguments=arguments,
                        ))
                        content_blocks.append({
                            "type": "tool_use",
                            "id": current_tool_id,
                            "name": current_tool_name,
                            "input": arguments if isinstance(arguments, dict) else {},
                        })
                        current_tool_name = ""
                        current_tool_id = ""
                        current_tool_json_parts = []

                elif event_type == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage:
                        usage_dict = self._as_dict(usage)
                        if isinstance(usage_dict, dict):
                            input_tokens = max(input_tokens, self._coerce_token_count(usage_dict.get("input_tokens")))
                            output_tokens = max(output_tokens, self._coerce_token_count(usage_dict.get("output_tokens")))

                elif event_type == "message_start":
                    msg = getattr(event, "message", None)
                    if msg:
                        msg_dict = self._as_dict(msg)
                        usage = msg_dict.get("usage", {})
                        if isinstance(usage, dict):
                            input_tokens = max(input_tokens, self._coerce_token_count(usage.get("input_tokens")))
                            output_tokens = max(output_tokens, self._coerce_token_count(usage.get("output_tokens")))

        # ── assemble result ────────────────────────────────────────────────
        full_text = "".join(collected_chunks).strip()
        if full_text:
            final_text_parts.append(full_text)

        if tool_calls:
            # Build assistant message with both text and tool_use blocks
            assistant_content: list[dict[str, Any]] = []
            if full_text:
                assistant_content.append({"type": "text", "text": full_text})
            assistant_content.extend(content_blocks)
            assistant_messages.append({
                "role": "assistant",
                "content": assistant_content,
            })
        elif full_text:
            assistant_messages.append({"role": "assistant", "content": full_text})

        token_usage = self._normalize_token_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return ProviderTurnResult(
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            final_text="".join(final_text_parts).strip(),
            consumed_tokens=token_usage.consumed_tokens,
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
        )

    def _gemini_fetch_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any],
        response_format: ResponseFormat | None,
        callback: Callable[[dict[str, Any]], None] | None,
        verbose: bool,
        run_id: str,
        iteration: int,
        toolkit: BaseToolkit,
        emit_stream: bool,
    ) -> ProviderTurnResult:
        google_genai_module = providers.google_genai
        if google_genai_module is None:
            raise ImportError("google-genai package is required for gemini provider — pip install google-genai")
        if not self.api_key:
            raise ValueError("error: api_key is required for gemini provider")

        client = google_genai_module.Client(api_key=self.api_key)
        request_payload = self._merged_payload(payload)

        # ── separate system instruction from messages ──────────────────────
        system_instruction: str | None = None
        chat_contents: list[dict[str, Any]] = []
        # System messages were already stripped by _project_canonical_to_gemini,
        # but if raw messages contain system roles (e.g. from observation pipeline),
        # extract them here.
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                system_instruction = msg.get("content", "")
            else:
                chat_contents.append(msg)

        # ── build tools for Gemini format ──────────────────────────────────
        tools_json = toolkit.to_json()
        gemini_tools = None
        supports_tools = bool(self._model_capability("supports_tools", True))
        if tools_json and supports_tools:
            function_declarations = []
            for t in tools_json:
                func_decl: dict[str, Any] = {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                }
                params = t.get("parameters")
                if params:
                    func_decl["parameters"] = params
                function_declarations.append(func_decl)
            gemini_tools = [{"function_declarations": function_declarations}]

        # ── build generation config ────────────────────────────────────────
        generation_config: dict[str, Any] = {}
        for key in ("temperature", "top_p", "top_k", "max_output_tokens"):
            if key in request_payload:
                generation_config[key] = request_payload[key]

        if response_format is not None:
            gemini_format = response_format.to_gemini()
            generation_config["response_mime_type"] = gemini_format.get("response_mime_type", "application/json")
            generation_config["response_schema"] = gemini_format.get("response_schema")

        # ── build request kwargs ───────────────────────────────────────────
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "contents": chat_contents,
        }
        if system_instruction:
            request_kwargs["config"] = {
                "system_instruction": system_instruction,
            }
        else:
            request_kwargs["config"] = {}

        if generation_config:
            request_kwargs["config"].update(generation_config)
        if gemini_tools:
            request_kwargs["config"]["tools"] = gemini_tools

        self._emit_request_messages(
            callback=callback,
            run_id=run_id,
            iteration=iteration,
            provider="gemini",
            messages=chat_contents,
            system=system_instruction if isinstance(system_instruction, str) else None,
            tool_names=self._tool_names_for_trace(tools_json),
        )

        # ── stream response ────────────────────────────────────────────────
        collected_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        assistant_messages: list[dict[str, Any]] = []
        final_text_parts: list[str] = []
        token_usage = TokenUsage()

        try:
            response_stream = client.models.generate_content_stream(**request_kwargs)
            for chunk in response_stream:
                # Extract usage metadata
                usage_meta = getattr(chunk, "usage_metadata", None)
                if usage_meta:
                    chunk_usage = self._extract_gemini_token_usage(usage_meta)
                    if chunk_usage.consumed_tokens >= token_usage.consumed_tokens:
                        token_usage = chunk_usage

                candidates = getattr(chunk, "candidates", None) or []
                for candidate in candidates:
                    content = getattr(candidate, "content", None)
                    if content is None:
                        continue
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        # Text part
                        text = getattr(part, "text", None)
                        if text:
                            collected_chunks.append(text)
                            if verbose and clear_output is not None:
                                clear_output(wait=True)
                                print("".join(collected_chunks))
                            if emit_stream:
                                self._emit(
                                    callback,
                                    "token_delta",
                                    run_id,
                                    iteration=iteration,
                                    provider="gemini",
                                    delta=text,
                                    accumulated_text="".join(collected_chunks),
                                )
                            continue

                        # Function call part
                        fn_call = getattr(part, "function_call", None)
                        if fn_call:
                            fn_name = getattr(fn_call, "name", "") or ""
                            fn_args = getattr(fn_call, "args", {}) or {}
                            # Convert to regular dict if it's a proto MapComposite
                            if not isinstance(fn_args, dict):
                                try:
                                    fn_args = dict(fn_args)
                                except Exception:
                                    fn_args = {}
                            call_id = str(uuid.uuid4())
                            tool_calls.append(ToolCall(
                                call_id=call_id,
                                name=fn_name,
                                arguments=fn_args,
                            ))
                            continue
        except Exception as exc:
            if collected_chunks:
                # Partial text received before error — return what we have
                full_text = "".join(collected_chunks).strip()
                return ProviderTurnResult(
                    assistant_messages=[{"role": "assistant", "content": full_text}],
                    tool_calls=[],
                    final_text=full_text,
                    consumed_tokens=token_usage.consumed_tokens,
                    input_tokens=token_usage.input_tokens,
                    output_tokens=token_usage.output_tokens,
                )
            raise ValueError(f"error: Gemini API call failed: {exc} ( runtime.engine -> _gemini_fetch_once )") from exc

        # ── assemble result ────────────────────────────────────────────────
        full_text = "".join(collected_chunks).strip()
        if full_text:
            final_text_parts.append(full_text)

        if tool_calls:
            # Build assistant message with function_call parts for conversation history
            assistant_parts: list[dict[str, Any]] = []
            if full_text:
                assistant_parts.append({"type": "text", "text": full_text})
            for tc in tool_calls:
                assistant_parts.append({
                    "type": "tool_use",
                    "id": tc.call_id,
                    "name": tc.name,
                    "input": tc.arguments if isinstance(tc.arguments, dict) else {},
                })
            assistant_messages.append({
                "role": "assistant",
                "content": assistant_parts,
            })
        elif full_text:
            assistant_messages.append({"role": "assistant", "content": full_text})

        return ProviderTurnResult(
            assistant_messages=assistant_messages,
            tool_calls=tool_calls,
            final_text="".join(final_text_parts).strip(),
            consumed_tokens=token_usage.consumed_tokens,
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
        )

    def _execute_tool_calls(
        self,
        *,
        tool_calls: list[ToolCall],
        run_id: str,
        iteration: int,
        callback: Callable[[dict[str, Any]], None] | None,
        on_tool_confirm: Callable | None = None,
        session_id: str | None = None,
        toolkits: list[BaseToolkit] | None = None,
    ) -> ToolExecutionOutcome:
        result_messages: list[dict[str, Any]] = []
        should_observe = False
        includes_human_input = any(
            is_human_input_tool_name(tool_call.name)
            for tool_call in tool_calls
        )

        if includes_human_input and len(tool_calls) > 1:
            error_text = "ask_user_question must be the only tool call in a turn"
            for tool_call in tool_calls:
                self._emit(
                    callback,
                    "tool_call",
                    run_id,
                    iteration=iteration,
                    tool_name=tool_call.name,
                    call_id=tool_call.call_id,
                    arguments=tool_call.arguments,
                )
                tool_result = {
                    "error": error_text,
                    "tool": tool_call.name,
                }
                result_messages.append(
                    self._build_tool_message(
                        tool_call=tool_call,
                        tool_result=tool_result,
                    )
                )
                self._emit(
                    callback,
                    "tool_result",
                    run_id,
                    iteration=iteration,
                    tool_name=tool_call.name,
                    call_id=tool_call.call_id,
                    result=tool_result,
                )
            return ToolExecutionOutcome(
                result_messages=result_messages,
                should_observe=False,
            )

        for tool_call in tool_calls:
            self._emit(
                callback,
                "tool_call",
                run_id,
                iteration=iteration,
                tool_name=tool_call.name,
                call_id=tool_call.call_id,
                arguments=tool_call.arguments,
            )

            if is_human_input_tool_name(tool_call.name):
                try:
                    request = HumanInputRequest.from_tool_arguments(
                        tool_call.arguments,
                        request_id=tool_call.call_id,
                    )
                except Exception as exc:
                    tool_result = {
                        "error": str(exc),
                        "tool": tool_call.name,
                    }
                    result_messages.append(
                        self._build_tool_message(
                            tool_call=tool_call,
                            tool_result=tool_result,
                        )
                    )
                    self._emit(
                        callback,
                        "tool_result",
                        run_id,
                        iteration=iteration,
                        tool_name=tool_call.name,
                        call_id=tool_call.call_id,
                        result=tool_result,
                    )
                    continue

                self._emit(
                    callback,
                    "human_input_requested",
                    run_id,
                    iteration=iteration,
                    request_id=request.request_id,
                    kind=request.kind,
                    title=request.title,
                    question=request.question,
                    selection_mode=request.selection_mode,
                    options=[option.to_dict() for option in request.options],
                    allow_other=request.allow_other,
                    other_label=request.other_label,
                    other_placeholder=request.other_placeholder,
                    min_selected=request.min_selected,
                    max_selected=request.max_selected,
                )
                return ToolExecutionOutcome(
                    result_messages=[],
                    should_observe=False,
                    awaiting_human_input=True,
                    human_input_request=request,
                )

            tool_obj = self._find_tool(tool_call.name, toolkits=toolkits)
            if tool_obj is not None and tool_obj.observe:
                should_observe = True

            # ── confirmation gate ──────────────────────────────────────
            effective_arguments = tool_call.arguments
            denied = False
            deny_reason = ""

            if (
                tool_obj is not None
                and tool_obj.requires_confirmation
                and on_tool_confirm is not None
            ):
                confirmation_request = ToolConfirmationRequest(
                    tool_name=tool_call.name,
                    call_id=tool_call.call_id,
                    arguments=tool_call.arguments if isinstance(tool_call.arguments, dict) else {},
                    description=tool_obj.description,
                )
                raw_response = on_tool_confirm(confirmation_request)
                response = ToolConfirmationResponse.from_raw(raw_response)

                if not response.approved:
                    denied = True
                    deny_reason = response.reason
                    self._emit(
                        callback,
                        "tool_denied",
                        run_id,
                        iteration=iteration,
                        tool_name=tool_call.name,
                        call_id=tool_call.call_id,
                        reason=deny_reason,
                    )
                else:
                    if response.modified_arguments is not None:
                        effective_arguments = response.modified_arguments
                    self._emit(
                        callback,
                        "tool_confirmed",
                        run_id,
                        iteration=iteration,
                        tool_name=tool_call.name,
                        call_id=tool_call.call_id,
                    )

            if denied:
                tool_result = {
                    "denied": True,
                    "tool": tool_call.name,
                    "reason": deny_reason or "User denied execution.",
                }
            else:
                tool_result = self._execute_from_toolkits(
                    tool_call.name,
                    effective_arguments,
                    session_id=session_id,
                    toolkits=toolkits,
                )
            result_messages.append(
                self._build_tool_message(
                    tool_call=tool_call,
                    tool_result=tool_result,
                )
            )

            self._emit(
                callback,
                "tool_result",
                run_id,
                iteration=iteration,
                tool_name=tool_call.name,
                call_id=tool_call.call_id,
                result=tool_result,
            )

        return ToolExecutionOutcome(
            result_messages=result_messages,
            should_observe=should_observe,
        )

    def _coerce_tool_execution_outcome(
        self,
        execution: ToolExecutionOutcome | tuple[Any, ...] | list[Any],
    ) -> ToolExecutionOutcome:
        if isinstance(execution, ToolExecutionOutcome):
            return execution

        if isinstance(execution, (tuple, list)):
            if len(execution) == 2:
                result_messages, should_observe = execution
                return ToolExecutionOutcome(
                    result_messages=list(result_messages or []),
                    should_observe=bool(should_observe),
                )

            if len(execution) == 4:
                result_messages, should_observe, awaiting_human_input, human_input_request = execution
                request = human_input_request
                if isinstance(request, dict):
                    request = HumanInputRequest.from_dict(request)
                elif request is not None and not isinstance(request, HumanInputRequest):
                    raise TypeError(
                        "legacy _execute_tool_calls human_input_request must be a HumanInputRequest or dict"
                    )
                return ToolExecutionOutcome(
                    result_messages=list(result_messages or []),
                    should_observe=bool(should_observe),
                    awaiting_human_input=bool(awaiting_human_input),
                    human_input_request=request,
                )

        raise TypeError(
            "_execute_tool_calls must return ToolExecutionOutcome or a legacy tuple/list "
            "of (result_messages, should_observe)"
        )

    def _observe_tool_batch(
        self,
        *,
        full_messages: list[dict[str, Any]],
        tool_messages: list[dict[str, Any]],
        payload: dict[str, Any],
    ) -> tuple[str, TokenUsage]:
        observe_messages = self._build_observation_messages(full_messages, tool_messages)
        observe_payload = self._build_observation_payload(payload)

        try:
            observe_turn = self._fetch_once(
                messages=observe_messages,
                payload=observe_payload,
                response_format=None,
                callback=None,
                verbose=False,
                run_id="observe",
                iteration=0,
                toolkit=BaseToolkit(),
                emit_stream=False,
                previous_response_id=None,
            )
        except Exception as exc:
            # Observation is optional: never let review pass break the main run.
            if self.provider == "anthropic" and "tool_result" in str(exc):
                return "", TokenUsage()
            raise

        observe_usage = self._normalize_token_usage(
            consumed_tokens=observe_turn.consumed_tokens,
            input_tokens=observe_turn.input_tokens,
            output_tokens=observe_turn.output_tokens,
        )
        if observe_turn.final_text:
            return observe_turn.final_text.strip(), observe_usage

        return self._last_assistant_text(observe_turn.assistant_messages).strip(), observe_usage

    def _is_anthropic_tool_result_message(self, message: dict[str, Any]) -> bool:
        if not isinstance(message, dict) or message.get("role") != "user":
            return False
        content = message.get("content")
        if not isinstance(content, list) or not content:
            return False
        return any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)

    def _find_anthropic_matching_tool_use_message(
        self,
        full_messages: list[dict[str, Any]],
        tool_use_ids: set[str],
    ) -> dict[str, Any] | None:
        for message in reversed(full_messages):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("id") in tool_use_ids:
                    return message
        return None

    def _build_observation_messages(
        self,
        full_messages: list[dict[str, Any]],
        tool_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        recent = full_messages[-OBSERVATION_RECENT_MESSAGES:]
        if self.provider == "anthropic":
            recent = [msg for msg in recent if not self._is_anthropic_tool_result_message(msg)]

            current_tool_use_ids = {
                block.get("tool_use_id")
                for tool_msg in tool_messages
                if isinstance(tool_msg, dict)
                for block in (tool_msg.get("content") if isinstance(tool_msg.get("content"), list) else [])
                if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)
            }
            if current_tool_use_ids:
                matching_tool_use_msg = self._find_anthropic_matching_tool_use_message(
                    full_messages,
                    current_tool_use_ids,
                )
                if matching_tool_use_msg is not None and matching_tool_use_msg not in recent:
                    recent.append(matching_tool_use_msg)

        observe_messages: list[dict[str, Any]] = [
            {"role": "system", "content": OBSERVATION_SYSTEM_PROMPT},
            *recent,
            *tool_messages,
            {
                "role": "user",
                "content": "Review the LAST tool result above and provide one brief actionable observation.",
            },
        ]
        return observe_messages

    def _build_observation_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        observe_payload = dict(payload or {})
        observe_payload["temperature"] = 0.2
        if self.provider == "openai":
            observe_payload["max_output_tokens"] = OBSERVATION_MAX_OUTPUT_TOKENS
        if self.provider == "ollama":
            observe_payload["num_predict"] = OBSERVATION_MAX_OUTPUT_TOKENS
        if self.provider == "anthropic":
            observe_payload["max_tokens"] = OBSERVATION_MAX_OUTPUT_TOKENS
        if self.provider == "gemini":
            observe_payload["max_output_tokens"] = OBSERVATION_MAX_OUTPUT_TOKENS
        return observe_payload

    def _build_memory_summary_payload(self, max_summary_chars: int) -> dict[str, Any]:
        max_output_tokens = max(64, min(512, max_summary_chars // 2))
        summary_payload: dict[str, Any] = {}
        if self.provider == "openai":
            summary_payload["max_output_tokens"] = max_output_tokens
            summary_payload["store"] = False
        if self.provider == "ollama":
            summary_payload["num_predict"] = max_output_tokens
        if self.provider == "anthropic":
            summary_payload["max_tokens"] = max_output_tokens
        if self.provider == "gemini":
            summary_payload["max_output_tokens"] = max_output_tokens
        return summary_payload

    def _generate_memory_summary(
        self,
        previous_summary: str,
        messages: list[dict[str, Any]],
        max_summary_chars: int,
        model: str,
    ) -> str:
        del model

        transcript_lines: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role") or message.get("type") or "item"
            role_text = role if isinstance(role, str) else str(role)
            if "content" in message:
                content_text = self._content_to_text(message.get("content", ""))
            else:
                content_text = json.dumps(message, default=str, ensure_ascii=False)
            content_text = content_text.strip()
            if not content_text:
                continue
            transcript_lines.append(f"{role_text}: {content_text}")

        transcript = "\n".join(transcript_lines)
        max_transcript_chars = max(1000, max_summary_chars * 6)
        if len(transcript) > max_transcript_chars:
            transcript = transcript[-max_transcript_chars:]

        summary_system_prompt = (
            "You write compact memory summaries for an AI assistant. "
            "Keep stable user preferences, key decisions, unresolved TODOs, and constraints. "
            "Do not include filler. Use short bullet points."
        )

        prior = previous_summary.strip()
        summary_user_prompt = (
            f"Previous summary:\n{prior or '(none)'}\n\n"
            f"New conversation chunk:\n{transcript}\n\n"
            f"Produce an updated memory summary within {max_summary_chars} characters."
        )

        summary_messages = [
            {"role": "system", "content": summary_system_prompt},
            {"role": "user", "content": summary_user_prompt},
        ]
        summary_turn = self._fetch_once(
            messages=summary_messages,
            payload=self._build_memory_summary_payload(max_summary_chars),
            response_format=None,
            callback=None,
            verbose=False,
            run_id="memory_summary",
            iteration=0,
                toolkit=BaseToolkit(),
            emit_stream=False,
            previous_response_id=None,
        )
        summary_text = (summary_turn.final_text or self._last_assistant_text(summary_turn.assistant_messages)).strip()
        if len(summary_text) > max_summary_chars:
            summary_text = summary_text[:max_summary_chars].rstrip()
        return summary_text

    def _build_long_term_extraction_payload(
        self,
        *,
        max_profile_chars: int,
        max_fact_items: int,
        max_episode_items: int = 0,
        max_playbook_items: int = 0,
    ) -> dict[str, Any]:
        max_output_tokens = max(
            192,
            min(
                1_200,
                max_profile_chars // 2
                + max_fact_items * 64
                + max_episode_items * 96
                + max_playbook_items * 128,
            ),
        )
        extraction_payload: dict[str, Any] = {}
        if self.provider == "openai":
            extraction_payload["max_output_tokens"] = max_output_tokens
            extraction_payload["store"] = False
        if self.provider == "ollama":
            extraction_payload["num_predict"] = max_output_tokens
        if self.provider == "anthropic":
            extraction_payload["max_tokens"] = max_output_tokens
        if self.provider == "gemini":
            extraction_payload["max_output_tokens"] = max_output_tokens
        return extraction_payload

    def _parse_long_term_extraction_result(self, raw_text: str) -> dict[str, Any]:
        payload_text = (raw_text or "").strip()
        if not payload_text:
            raise ValueError("long_term_extraction_invalid_json: empty response")

        try:
            parsed = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"long_term_extraction_invalid_json: {exc.msg}") from exc

        if not isinstance(parsed, dict):
            raise ValueError(
                f"long_term_extraction_invalid_top_level: expected JSON object, got {type(parsed).__name__}"
            )

        return {
            "profile_patch": copy.deepcopy(parsed.get("profile_patch", {})),
            "facts": copy.deepcopy(parsed.get("facts", [])),
            "episodes": copy.deepcopy(parsed.get("episodes", [])),
            "playbooks": copy.deepcopy(parsed.get("playbooks", [])),
        }

    def _extract_long_term_memory(
        self,
        previous_profile: dict[str, Any],
        messages: list[dict[str, Any]],
        max_profile_chars: int,
        max_fact_items: int,
        model: str,
        config: Any | None = None,
    ) -> dict[str, Any]:
        del model
        max_episode_items = max(0, int(getattr(config, "max_episode_items", 3) or 3))
        max_playbook_items = max(0, int(getattr(config, "max_playbook_items", 2) or 2))

        transcript_lines: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role") or message.get("type") or "item"
            role_text = role if isinstance(role, str) else str(role)
            content_text = self._content_to_text(message.get("content", ""))
            content_text = content_text.strip()
            if not content_text:
                continue
            transcript_lines.append(f"{role_text}: {content_text}")

        transcript = "\n".join(transcript_lines)
        max_transcript_chars = max(1200, max_profile_chars * 8)
        if len(transcript) > max_transcript_chars:
            transcript = transcript[-max_transcript_chars:]

        previous_profile_json = json.dumps(previous_profile or {}, ensure_ascii=False)
        extraction_format = None
        if self.provider != "openai":
            extraction_format = ResponseFormat(
                name="long_term_memory_extraction",
                schema={
                    "type": "object",
                    "properties": {
                        "profile_patch": {
                            "type": "object",
                            "description": "Stable user identity, preferences, and long-lived constraints only.",
                            "additionalProperties": True,
                        },
                        "facts": {
                            "type": "array",
                            "description": "Reusable long-term facts, project context, decisions, or entity relationships worth semantic recall later.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "subtype": {
                                        "type": "string",
                                        "enum": ["fact", "decision", "project_context", "entity", "event"],
                                    },
                                    "text": {"type": "string"},
                                },
                                "required": ["text"],
                                "additionalProperties": False,
                            },
                        },
                        "episodes": {
                            "type": "array",
                            "description": "Past cases that include enough situation, action, and outcome detail to be reusable later.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "situation": {"type": "string"},
                                    "action": {"type": "string"},
                                    "outcome": {"type": "string"},
                                },
                                "required": ["situation", "action", "outcome"],
                                "additionalProperties": False,
                            },
                        },
                        "playbooks": {
                            "type": "array",
                            "description": "Reusable workflows or procedures with trigger, goal, steps, and caveats.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "trigger": {"type": "string"},
                                    "goal": {"type": "string"},
                                    "steps": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "caveats": {"type": "string"},
                                },
                                "required": ["trigger", "goal", "steps"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["profile_patch", "facts", "episodes", "playbooks"],
                    "additionalProperties": False,
                },
                required=["profile_patch", "facts", "episodes", "playbooks"],
            )

        extraction_system_prompt = (
            "You extract long-term memory for an AI assistant. "
            "Return only stable user identity details, durable preferences, explicit constraints, "
            "reusable long-term facts, reusable past episodes, and reusable workflows. "
            "Ignore transient requests, small talk, and details that are unlikely to matter in future conversations."
        )
        extraction_user_prompt = (
            f"Existing profile JSON:\n{previous_profile_json}\n\n"
            f"New conversation chunk:\n{transcript or '(none)'}\n\n"
            f"Return JSON only. Rules:\n"
            f"- Return one top-level object with keys `profile_patch`, `facts`, `episodes`, and `playbooks`.\n"
            f"- `profile_patch` should be an object with only durable updates.\n"
            f"- Keep the updated profile compact enough to stay within roughly {max_profile_chars} characters when merged.\n"
            f"- `facts` should contain at most {max_fact_items} items.\n"
            f"- Each fact must be future-reusable and self-contained.\n"
            f"- `episodes` should contain at most {max_episode_items} items and only include cases with clear situation, action, and outcome.\n"
            f"- `playbooks` should contain at most {max_playbook_items} items and only include reusable procedures, not one-off improvisation.\n"
            f"- Prefer stable long-lived information over short-lived details.\n"
            f"- Use an empty object/list when there is nothing worth saving."
        )

        extraction_messages = [
            {"role": "system", "content": extraction_system_prompt},
            {"role": "user", "content": extraction_user_prompt},
        ]
        extraction_turn = self._fetch_once(
            messages=extraction_messages,
            payload=self._build_long_term_extraction_payload(
                max_profile_chars=max_profile_chars,
                max_fact_items=max_fact_items,
                max_episode_items=max_episode_items,
                max_playbook_items=max_playbook_items,
            ),
            response_format=extraction_format,
            openai_text_format={"type": "json_object"} if self.provider == "openai" else None,
            callback=None,
            verbose=False,
            run_id="long_term_memory",
            iteration=0,
            toolkit=BaseToolkit(),
            emit_stream=False,
            previous_response_id=None,
        )
        raw_text = (
            extraction_turn.final_text
            or self._last_assistant_text(extraction_turn.assistant_messages)
        ).strip()
        return self._parse_long_term_extraction_result(raw_text)

    def _inject_observation(self, tool_message: dict[str, Any], observation: str):
        content_key = "content" if "content" in tool_message else "output"
        existing = tool_message.get(content_key, "")

        try:
            parsed = json.loads(existing) if isinstance(existing, str) and existing.strip() else {}
            if not isinstance(parsed, dict):
                parsed = {"result": parsed}
            parsed["observation"] = observation
            tool_message[content_key] = json.dumps(parsed, default=str, ensure_ascii=False)
        except Exception:
            suffix = f"\n[OBSERVATION] {observation}"
            tool_message[content_key] = f"{existing}{suffix}" if existing else suffix.strip()

    def _apply_response_format(
        self,
        messages: list[dict[str, Any]],
        response_format: ResponseFormat | None,
    ):
        if response_format is None:
            return

        for message in reversed(messages):
            if message.get("role") != "assistant":
                continue
            raw_content = self._content_to_text(message.get("content", ""))
            parsed = response_format.parse(raw_content)
            message["content"] = json.dumps(parsed, ensure_ascii=False)
            return

    def _extract_openai_message_text(self, item: dict[str, Any]) -> str:
        content = item.get("content") or []
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("output_text", "text"):
                text = block.get("text", "")
                if text:
                    text_parts.append(text)
                continue
            if block.get("type") == "refusal":
                refusal_text = block.get("refusal", "")
                if refusal_text:
                    text_parts.append(refusal_text if isinstance(refusal_text, str) else str(refusal_text))
        return "".join(text_parts)

    def _as_dict(self, obj: Any) -> dict[str, Any]:
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "__dict__") and obj.__dict__:
            return dict(obj.__dict__)
        # Fall back to public attributes (covers classes with only class-level attrs)
        attrs = {
            k: getattr(obj, k)
            for k in dir(obj)
            if not k.startswith("_") and not callable(getattr(obj, k, None))
        }
        if attrs:
            return attrs
        return {"value": str(obj)}

    def _extract_openai_token_usage(self, usage: Any) -> TokenUsage:
        if usage is None:
            return TokenUsage()

        usage_dict = self._as_dict(usage)
        return self._normalize_token_usage(
            consumed_tokens=usage_dict.get("total_tokens"),
            input_tokens=usage_dict.get("input_tokens"),
            output_tokens=usage_dict.get("output_tokens"),
        )

    def _extract_gemini_token_usage(self, usage_metadata: Any) -> TokenUsage:
        if usage_metadata is None:
            return TokenUsage()

        usage_dict = self._as_dict(usage_metadata)
        prompt_tokens = self._coerce_token_count(usage_dict.get("prompt_token_count"))
        tool_use_prompt_tokens = self._coerce_token_count(usage_dict.get("tool_use_prompt_token_count"))
        candidate_tokens = self._coerce_token_count(usage_dict.get("candidates_token_count"))
        thought_tokens = self._coerce_token_count(usage_dict.get("thoughts_token_count"))
        return self._normalize_token_usage(
            consumed_tokens=usage_dict.get("total_token_count"),
            input_tokens=prompt_tokens + tool_use_prompt_tokens,
            output_tokens=candidate_tokens + thought_tokens,
        )

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in ("text", "output_text", "input_text"):
                    text = block.get("text", "")
                    if text:
                        parts.append(text if isinstance(text, str) else str(text))
            return "".join(parts)
        if content is None:
            return ""
        return str(content)

    def _last_assistant_text(self, messages: list[dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                return self._content_to_text(msg.get("content", "")).strip()
        return ""

__all__ = [
    "ProviderTurnResult",
    "ToolCall",
    "ToolExecutionOutcome",
    "Broth",
]
