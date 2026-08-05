from __future__ import annotations

import copy
import logging
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..context import ContextRuntime

from ..memory import (
    ExecutionCheckpointResumeRequiredError,
    KernelMemoryRuntime,
    MemoryRuntimeComponentMode,
)
from ..memory.checkpoint_state import restore_resume_checkpoint_messages
from ..memory.ownership import ensure_no_external_provider_history
from ..execution import ExecutionGuard
from ..kernel.loop import KernelLoop
from ..kernel.model_io import ModelIO
from ..kernel.replay_handle import load_provider_replay_handle
from ..kernel.run_preparation import effective_payload_store, infer_model, infer_provider
from ..kernel.types import KernelRunResult
from ..interaction.durable import (
    INTERACTION_KIND_HUMAN_INPUT,
    InteractionIntegrityError,
    InteractionNotPendingError,
    InteractionReceipt,
)
from ..schemas import ResponseFormat
from ..runtime import (
    CompletionPolicy,
    CompletionPolicyRunner,
    build_runtime_loop,
)
from ..runtime.module_context import AgentRuntimeContext
from ..tools import Tool, Toolkit
from ..tools.exposure import ToolExposureRuntime, ToolOptimizerConfig
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
    runtime_context: AgentRuntimeContext | None = None
    execution_owner_id: str | None = None
    execution_guard: ExecutionGuard | None = None
    tool_runtime_config: dict[str, Any] | None = None
    interaction_id: str | None = None
    submitted_by: str = "user"

    def __post_init__(self) -> None:
        runtime_context = self.runtime_context
        if runtime_context is None:
            return
        if not isinstance(runtime_context, AgentRuntimeContext):
            raise TypeError("runtime_context must be an AgentRuntimeContext")
        identity = runtime_context.identity
        for field_name, identity_value in (
            ("session_id", identity.execution_id),
            ("run_id", identity.run_id),
            ("execution_owner_id", identity.attempt_id),
        ):
            supplied = getattr(self, field_name)
            if supplied is None or supplied == "":
                setattr(self, field_name, identity_value)
                continue
            if supplied != identity_value:
                raise ValueError(
                    f"runtime_context identity does not match {field_name}"
                )


@dataclass
class PreparedAgent:
    loop: KernelLoop
    toolkit: Toolkit
    spec: AgentSpec
    state: AgentState
    call_context: AgentCallContext
    memory_runtime: KernelMemoryRuntime | None = None
    memory_runtime_component_mode: MemoryRuntimeComponentMode = (
        MemoryRuntimeComponentMode.FULL
    )
    default_payload: dict[str, Any] = field(default_factory=dict)
    default_response_format: ResponseFormat | None = None
    default_max_iterations: int | None = None
    default_max_context_window_tokens: int | None = None
    default_on_tool_confirm: Callable[..., Any] | None = None
    default_on_human_input: Callable[..., Any] | None = None
    default_on_max_iterations: Callable[..., Any] | None = None
    run_hooks: list[RunHook] = field(default_factory=list)
    tool_runtime_plugins: list[Any] = field(default_factory=list)
    tool_optimizer_config: ToolOptimizerConfig | None = None
    completion_policy: CompletionPolicy | None = None
    session_history_owned_by_memory: bool = False
    semantic_context_owner: str | None = None
    context_runtime: "ContextRuntime | None" = None
    _completion_replay_frame: dict[str, Any] | None = field(
        default=None,
        init=False,
        repr=False,
    )

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

    def _prepare_tool_exposure(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any] | None,
        provider: str | None = None,
        model: str | None = None,
        execution_guard: ExecutionGuard | None = None,
        replay_plan: dict[str, Any] | None = None,
        require_durable_plan: bool = False,
    ) -> tuple[Toolkit, list[Any]]:
        plugins = list(self.tool_runtime_plugins)
        config = ToolOptimizerConfig.coerce(self.tool_optimizer_config)
        if replay_plan is not None and (config is None or not config.enabled):
            raise InteractionIntegrityError(
                "durable tool exposure replay requires the tool optimizer"
            )
        if config is None or not config.enabled:
            return self.toolkit, plugins
        if (
            require_durable_plan
            and replay_plan is None
            and len(self.toolkit.tools) > config.trigger_tool_count
        ):
            raise InteractionIntegrityError(
                "durable interaction continuation is missing its exposure plan"
            )

        model_io = self.loop.model_io
        if model_io is None:
            if replay_plan is not None:
                raise InteractionIntegrityError(
                    "durable tool exposure replay requires an active model runtime"
                )
            return self.toolkit, plugins
        if execution_guard is not None:
            execution_guard.renew()
            model_io = execution_guard.guard_model_io(
                model_io,
                "model.tool_exposure",
            )

        runtime = ToolExposureRuntime(
            config=config,
            full_toolkit=self.toolkit,
            model_io=model_io,
            provider=provider or self.spec.provider,
            model=model or self.spec.model,
            messages=messages,
            instructions=self.spec.instructions,
            payload=payload,
            callback=self.call_context.callback,
            run_id=self.call_context.run_id,
        )
        exposed_toolkit = runtime.prepare(replay_plan=replay_plan)
        if execution_guard is not None:
            execution_guard.assert_active()
        plugins.extend(runtime.build_plugins())
        return exposed_toolkit, plugins

    def _assert_fresh_run_has_no_pending_interaction(self) -> None:
        session_id = str(self.call_context.session_id or "")
        if self.memory_runtime is None or not session_id:
            return
        checkpoint = self.memory_runtime.load_execution_checkpoint(session_id)
        if isinstance(checkpoint, dict) and isinstance(
            checkpoint.get("interaction_ref"),
            dict,
        ):
            raise ExecutionCheckpointResumeRequiredError(
                "session is awaiting a durable interaction; resume the persisted "
                "continuation instead of starting a fresh run"
            )

    def _preflight_durable_interaction_resume(
        self,
        *,
        session_id: str,
        checkpoint: dict[str, Any],
        continuation: dict[str, Any],
        response: Any,
        submitted_by: str,
        expected_kind: str | None,
        execution_guard: ExecutionGuard | None,
    ) -> None:
        interaction_runtime = self.loop.interaction_runtime
        interaction_ref = checkpoint.get("interaction_ref")
        if interaction_runtime is None or not isinstance(interaction_ref, dict):
            raise InteractionNotPendingError(
                "execution checkpoint has no durable interaction"
            )
        interaction_id = str(interaction_ref.get("interaction_id") or "")
        pending = interaction_runtime.load(
            session_id,
            interaction_id=interaction_id,
            require_active=True,
        )
        if (
            pending.checkpoint_id != checkpoint.get("checkpoint_id")
            or pending.request.request_digest
            != interaction_ref.get("request_digest")
        ):
            raise InteractionIntegrityError(
                "durable interaction is not bound to the active checkpoint"
            )
        subject = pending.request.subject
        if not isinstance(subject, dict):
            raise InteractionIntegrityError(
                "durable interaction has no runtime provider/model binding"
            )
        checkpoint_provider = str(checkpoint.get("provider") or "")
        checkpoint_model = str(checkpoint.get("model") or "")
        subject_provider = str(subject.get("provider") or "")
        subject_model = str(subject.get("model") or "")
        actual_provider = str(infer_provider(self.loop.model_io) or "")
        actual_model = str(infer_model(self.loop.model_io) or "")
        if (
            not checkpoint_provider
            or not checkpoint_model
            or checkpoint_provider != subject_provider
            or checkpoint_model != subject_model
        ):
            raise InteractionIntegrityError(
                "durable interaction provider/model binding does not match its checkpoint"
            )
        continuation_provider = str(continuation.get("provider") or "")
        continuation_model = str(continuation.get("model") or "")
        if (
            continuation_provider != checkpoint_provider
            or continuation_model != checkpoint_model
        ):
            raise InteractionIntegrityError(
                "durable interaction continuation provider/model does not match its checkpoint"
            )
        if (
            actual_provider != checkpoint_provider
            or actual_model != checkpoint_model
        ):
            raise InteractionIntegrityError(
                "durable interaction provider/model binding does not match the current model"
            )
        if expected_kind is not None and pending.request.kind != expected_kind:
            raise InteractionNotPendingError(
                f"pending interaction is not a {expected_kind.replace('_', '-')} request"
            )
        if continuation.get("interaction_id") != pending.request.interaction_id:
            raise InteractionIntegrityError(
                "resume continuation does not match the durable interaction"
            )
        if response is None:
            interaction_runtime.require_receipt(
                session_id,
                interaction_id=interaction_id,
            )
            return
        interaction_runtime.record_receipt(
            session_id,
            interaction_id=interaction_id,
            response=copy.deepcopy(response),
            submitted_by=submitted_by,
            expected_revision=pending.session_snapshot.revision,
            execution_fence=(
                execution_guard.fence
                if execution_guard is not None
                else None
            ),
        )

    def _run_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any] | None,
        previous_response_id: str | None,
        max_iterations: int,
        execution_guard: ExecutionGuard | None = None,
    ) -> KernelRunResult:
        if self.session_history_owned_by_memory:
            ensure_no_external_provider_history(
                provider=self.spec.provider,
                previous_response_id=previous_response_id,
            )
        self._assert_fresh_run_has_no_pending_interaction()
        toolkit, tool_runtime_plugins = self._prepare_tool_exposure(
            messages=messages,
            payload=payload,
            provider=self.spec.provider,
            model=self.spec.model,
            execution_guard=execution_guard,
        )
        result = self.loop.run(
            messages=messages,
            payload=payload,
            response_format=self._resolved_response_format(),
            callback=self.call_context.callback,
            verbose=self.call_context.verbose,
            max_iterations=max_iterations,
            previous_response_id=previous_response_id,
            on_tool_confirm=self._resolved_on_tool_confirm(),
            on_human_input=self._resolved_on_human_input(),
            on_max_iterations=self._resolved_on_max_iterations(),
            session_id=self.call_context.session_id,
            memory_namespace=self.call_context.memory_namespace,
            provider=self.spec.provider,
            model=self.spec.model,
            max_context_window_tokens=self._resolved_max_context_window_tokens(),
            toolkit=toolkit,
            run_id=self.call_context.run_id,
            tool_runtime_plugins=tool_runtime_plugins,
            tool_runtime_config=copy.deepcopy(self.call_context.tool_runtime_config or {}),
            _provider_replay_frame=copy.deepcopy(
                self._completion_replay_frame
            ),
            _execution_guard=execution_guard,
        )
        self._completion_replay_frame = load_provider_replay_handle(
            result.provider_replay_handle
        )
        return result

    def _run_completion_repair_once(
        self,
        *,
        messages: list[dict[str, Any]],
        payload: dict[str, Any] | None,
        previous_response_id: str | None,
        max_iterations: int,
        execution_guard: ExecutionGuard | None = None,
    ) -> KernelRunResult:
        if not self.session_history_owned_by_memory:
            use_remote_repair = bool(
                previous_response_id
                and self.spec.provider == "openai"
                and effective_payload_store(
                    self.loop.model_io,
                    dict(payload or {}),
                )
                is not False
            )
            if use_remote_repair:
                feedback = copy.deepcopy(messages[-1]) if messages else None
                if not isinstance(feedback, dict) or feedback.get("role") != "user":
                    raise ValueError(
                        "completion repair with previous_response_id must end with user feedback"
                    )
            else:
                previous_response_id = None
            return self._run_once(
                messages=messages,
                payload=payload,
                previous_response_id=previous_response_id,
                max_iterations=max_iterations,
                execution_guard=execution_guard,
            )
        feedback = copy.deepcopy(messages[-1]) if messages else None
        if not isinstance(feedback, dict) or feedback.get("role") != "user":
            raise ValueError("completion repair must end with a user feedback message")
        return self._run_once(
            messages=[feedback],
            payload=payload,
            previous_response_id=None,
            max_iterations=max_iterations,
            execution_guard=execution_guard,
        )

    def _completion_policy_runner(
        self,
        execution_guard: ExecutionGuard | None = None,
    ) -> CompletionPolicyRunner:
        def run_once(**kwargs: Any) -> KernelRunResult:
            return self._run_completion_repair_once(
                execution_guard=execution_guard,
                **kwargs,
            )

        return CompletionPolicyRunner(
            policy=self.completion_policy,
            run_once=run_once,
            event_callback=self.call_context.callback,
            run_id=str(self.call_context.run_id or "agent"),
        )

    def _execution_scope(self):
        inherited_guard = self.call_context.execution_guard
        session_id = str(self.call_context.session_id or "")
        if inherited_guard is not None:
            if not session_id:
                raise ValueError(
                    "an inherited execution guard requires a non-empty session_id"
                )
            if inherited_guard.lease.execution_id != session_id:
                raise ValueError(
                    "inherited execution guard does not belong to the call session_id"
                )
            inherited_guard.assert_active()
            return nullcontext(inherited_guard)
        execution_runtime = self.loop.execution_runtime
        if execution_runtime is None or not session_id:
            return nullcontext(None)
        owner_id = str(self.call_context.execution_owner_id or "").strip() or None
        return execution_runtime.scope(session_id, owner_id=owner_id)

    def run(self) -> KernelRunResult:
        messages = copy.deepcopy(self.call_context.input_messages or [])
        payload = self._merge_payloads(self.default_payload, self.call_context.payload)
        with self._execution_scope() as execution_guard:
            result = self._run_once(
                payload=payload,
                messages=messages,
                max_iterations=self._resolved_max_iterations(),
                previous_response_id=self.call_context.previous_response_id,
                execution_guard=execution_guard,
            )
            result = self._completion_policy_runner(execution_guard).apply(
                result,
                payload=payload,
                max_iterations=self._resolved_max_iterations(),
            )
            return self._apply_run_hooks(result)

    def resume_human_input(self) -> KernelRunResult:
        with self._execution_scope() as execution_guard:
            return self._resume_human_input_under_guard(execution_guard)

    def submit_interaction(self) -> InteractionReceipt:
        session_id = str(self.call_context.session_id or "")
        interaction_id = str(self.call_context.interaction_id or "")
        interaction_runtime = self.loop.interaction_runtime
        if interaction_runtime is None or not session_id or not interaction_id:
            raise InteractionNotPendingError(
                "submit_interaction requires a memory-backed session_id and interaction_id"
            )
        persisted = interaction_runtime.record_receipt(
            session_id,
            interaction_id=interaction_id,
            response=copy.deepcopy(self.call_context.response),
            submitted_by=str(self.call_context.submitted_by or "user"),
        )
        if persisted.receipt is None:
            raise InteractionNotPendingError(
                "interaction receipt was not persisted"
            )
        return persisted.receipt

    def resume_interaction(self) -> KernelRunResult:
        with self._execution_scope() as execution_guard:
            provided_conversation = self.call_context.conversation
            provided_continuation = self.call_context.continuation
            if (provided_conversation is None) != (provided_continuation is None):
                raise ValueError(
                    "conversation and continuation must either both be provided or both be omitted"
                )
            session_id = str(self.call_context.session_id or "")
            if self.memory_runtime is None or not session_id:
                raise ExecutionCheckpointResumeRequiredError(
                    "interaction resume requires a memory-backed session_id"
                )
            if provided_conversation is None:
                checkpoint = self.memory_runtime.load_execution_checkpoint(session_id)
                if not isinstance(checkpoint, dict) or not isinstance(
                    checkpoint.get("interaction_ref"),
                    dict,
                ):
                    raise ExecutionCheckpointResumeRequiredError(
                        "session has no durable interaction checkpoint"
                    )
                provided_conversation = copy.deepcopy(
                    checkpoint.get("transcript") or []
                )
                raw_continuation = checkpoint.get("continuation")
                if not isinstance(raw_continuation, dict):
                    raise ExecutionCheckpointResumeRequiredError(
                        "durable interaction checkpoint has no continuation"
                    )
                provided_continuation = copy.deepcopy(raw_continuation)

            checkpoint = self.memory_runtime.load_execution_checkpoint(session_id)
            if not isinstance(checkpoint, dict):
                raise ExecutionCheckpointResumeRequiredError(
                    "session has no durable interaction checkpoint"
                )
            restore_resume_checkpoint_messages(
                checkpoint,
                conversation=copy.deepcopy(provided_conversation or []),
                continuation=copy.deepcopy(provided_continuation or {}),
            )
            self._preflight_durable_interaction_resume(
                session_id=session_id,
                checkpoint=checkpoint,
                continuation=copy.deepcopy(provided_continuation or {}),
                response=self.call_context.response,
                submitted_by=str(
                    self.call_context.submitted_by or "api:resume_interaction"
                ),
                expected_kind=None,
                execution_guard=execution_guard,
            )

            continuation_payload = (
                provided_continuation.get("payload")
                if isinstance(provided_continuation, dict)
                else None
            )
            payload = self._merge_payloads(
                self.default_payload,
                continuation_payload
                if isinstance(continuation_payload, dict)
                else None,
                self.call_context.payload,
            )
            conversation = copy.deepcopy(provided_conversation or [])
            is_durable_continuation = (
                isinstance(provided_continuation, dict)
                and provided_continuation.get("type")
                in {
                    "human_input_continuation",
                    "tool_approval_continuation",
                    "max_budget_continuation",
                }
            )
            plan_present = bool(
                is_durable_continuation
                and isinstance(provided_continuation, dict)
                and "tool_exposure_plan" in provided_continuation
            )
            raw_exposure_plan = (
                provided_continuation.get("tool_exposure_plan")
                if plan_present and isinstance(provided_continuation, dict)
                else None
            )
            if plan_present and not isinstance(raw_exposure_plan, dict):
                raise InteractionIntegrityError(
                    "durable interaction exposure plan must be an object"
                )
            toolkit, tool_runtime_plugins = self._prepare_tool_exposure(
                messages=conversation,
                payload=payload,
                provider=(
                    str(provided_continuation.get("provider") or "")
                    if isinstance(provided_continuation, dict)
                    else None
                ),
                model=(
                    str(provided_continuation.get("model") or "")
                    if isinstance(provided_continuation, dict)
                    else None
                ),
                execution_guard=execution_guard,
                replay_plan=(
                    copy.deepcopy(raw_exposure_plan)
                    if isinstance(raw_exposure_plan, dict)
                    else None
                ),
                require_durable_plan=is_durable_continuation,
            )
            result = self.loop.resume_interaction(
                session_id=session_id,
                conversation=conversation,
                continuation=copy.deepcopy(provided_continuation or {}),
                response=None,
                submitted_by=str(
                    self.call_context.submitted_by or "api:resume_interaction"
                ),
                payload=payload,
                response_format=self._resolved_response_format(),
                callback=self.call_context.callback,
                verbose=self.call_context.verbose,
                on_tool_confirm=self._resolved_on_tool_confirm(),
                on_human_input=self._resolved_on_human_input(),
                on_max_iterations=self._resolved_on_max_iterations(),
                memory_namespace=self.call_context.memory_namespace,
                toolkit=toolkit,
                run_id=self.call_context.run_id,
                tool_runtime_plugins=tool_runtime_plugins,
                tool_runtime_config=copy.deepcopy(
                    self.call_context.tool_runtime_config or {}
                ),
                _execution_guard=execution_guard,
            )
            return self._apply_run_hooks(result)

    def _resume_human_input_under_guard(
        self,
        execution_guard: ExecutionGuard | None,
    ) -> KernelRunResult:
        provided_conversation = self.call_context.conversation
        provided_continuation = self.call_context.continuation
        checkpoint: dict[str, Any] | None = None
        session_id = str(self.call_context.session_id or "")
        if (provided_conversation is None) != (provided_continuation is None):
            raise ValueError(
                "conversation and continuation must either both be provided or both be omitted"
            )
        if self.memory_runtime is not None and session_id:
            checkpoint = self.memory_runtime.load_execution_checkpoint(session_id)
        if provided_conversation is None and provided_continuation is None:
            if self.memory_runtime is None or not session_id:
                raise ExecutionCheckpointResumeRequiredError(
                    "cold human-input resume requires a memory-backed session_id"
                )
            if (
                not isinstance(checkpoint, dict)
                or checkpoint.get("status") != "awaiting_human_input"
            ):
                raise ExecutionCheckpointResumeRequiredError(
                    "session has no awaiting_human_input execution checkpoint"
                )
            provided_conversation = copy.deepcopy(checkpoint.get("transcript") or [])
            raw_continuation = checkpoint.get("continuation")
            if not isinstance(raw_continuation, dict):
                raise ExecutionCheckpointResumeRequiredError(
                    "persisted human-input checkpoint has no continuation"
                )
            provided_continuation = copy.deepcopy(raw_continuation)

        response_for_loop = copy.deepcopy(self.call_context.response)
        if isinstance(checkpoint, dict):
            restore_resume_checkpoint_messages(
                checkpoint,
                conversation=copy.deepcopy(provided_conversation or []),
                continuation=copy.deepcopy(provided_continuation or {}),
            )
            if isinstance(checkpoint.get("interaction_ref"), dict):
                self._preflight_durable_interaction_resume(
                    session_id=session_id,
                    checkpoint=checkpoint,
                    continuation=copy.deepcopy(provided_continuation or {}),
                    response=self.call_context.response,
                    submitted_by="api:resume_human_input",
                    expected_kind=INTERACTION_KIND_HUMAN_INPUT,
                    execution_guard=execution_guard,
                )
                response_for_loop = None
        if response_for_loop is None and not (
            isinstance(checkpoint, dict)
            and isinstance(checkpoint.get("interaction_ref"), dict)
        ):
            raise InteractionNotPendingError(
                "human-input resume requires an explicit response"
            )

        continuation_payload = None
        if isinstance(provided_continuation, dict):
            raw_payload = provided_continuation.get("payload")
            continuation_payload = raw_payload if isinstance(raw_payload, dict) else None
        conversation = copy.deepcopy(provided_conversation or [])
        payload = self._merge_payloads(self.default_payload, continuation_payload, self.call_context.payload)
        durable_interaction = bool(
            isinstance(checkpoint, dict)
            and isinstance(checkpoint.get("interaction_ref"), dict)
        )
        plan_present = bool(
            isinstance(provided_continuation, dict)
            and "tool_exposure_plan" in provided_continuation
        )
        raw_exposure_plan = (
            provided_continuation.get("tool_exposure_plan")
            if plan_present and isinstance(provided_continuation, dict)
            else None
        )
        if plan_present and not isinstance(raw_exposure_plan, dict):
            raise InteractionIntegrityError(
                "durable interaction exposure plan must be an object"
            )
        toolkit, tool_runtime_plugins = self._prepare_tool_exposure(
            messages=conversation,
            payload=payload,
            provider=(
                str(provided_continuation.get("provider") or "")
                if isinstance(provided_continuation, dict)
                else None
            ),
            model=(
                str(provided_continuation.get("model") or "")
                if isinstance(provided_continuation, dict)
                else None
            ),
            execution_guard=execution_guard,
            replay_plan=(
                copy.deepcopy(raw_exposure_plan)
                if isinstance(raw_exposure_plan, dict)
                else None
            ),
            require_durable_plan=durable_interaction,
        )
        result = self.loop.resume_human_input(
            conversation=conversation,
            continuation=copy.deepcopy(provided_continuation or {}),
            response=response_for_loop,
            payload=payload,
            response_format=self._resolved_response_format(),
            callback=self.call_context.callback,
            verbose=self.call_context.verbose,
            on_tool_confirm=self._resolved_on_tool_confirm(),
            on_human_input=self._resolved_on_human_input(),
            on_max_iterations=self._resolved_on_max_iterations(),
            session_id=self.call_context.session_id,
            memory_namespace=self.call_context.memory_namespace,
            toolkit=toolkit,
            run_id=self.call_context.run_id,
            tool_runtime_plugins=tool_runtime_plugins,
            tool_runtime_config=copy.deepcopy(self.call_context.tool_runtime_config or {}),
            _execution_guard=execution_guard,
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
    memory_runtime_component_mode: MemoryRuntimeComponentMode = (
        MemoryRuntimeComponentMode.FULL
    )
    default_payload: dict[str, Any] = field(default_factory=dict)
    default_response_format: ResponseFormat | None = None
    default_max_iterations: int | None = None
    default_max_context_window_tokens: int | None = None
    default_on_tool_confirm: Callable[..., Any] | None = None
    default_on_human_input: Callable[..., Any] | None = None
    default_on_max_iterations: Callable[..., Any] | None = None
    run_hooks: list[RunHook] = field(default_factory=list)
    tool_runtime_plugins: list[Any] = field(default_factory=list)
    tool_optimizer_config: ToolOptimizerConfig | None = None
    completion_policy: CompletionPolicy | None = None
    semantic_context_owner: str | None = None
    context_runtime: "ContextRuntime | None" = None
    _model_io: ModelIO | None = None
    _model_io_factory: Callable[[AgentSpec, AgentCallContext], ModelIO] | None = None

    def _ensure_unique_tool_names(
        self,
        incoming_names: list[str],
        *,
        source_label: str,
    ) -> None:
        duplicates = sorted({name for name in incoming_names if name in self.toolkit.tools})
        if duplicates:
            raise ValueError(
                f"agent {self.spec.name!r} tool name conflict from {source_label}: "
                + ", ".join(duplicates)
                + ". Tool names must be unique across all configured tools and toolkits."
            )

    def add_tool(self, entry: Tool | Toolkit | Callable[..., Any]) -> None:
        if isinstance(entry, Toolkit):
            self._ensure_unique_tool_names(
                list(entry.tools.keys()),
                source_label=f"toolkit {entry.__class__.__name__}",
            )
            prompt_sections = tuple(getattr(entry, "prompt_sections", ()) or ())
            if prompt_sections:
                existing_sections = list(getattr(self.toolkit, "prompt_sections", ()) or ())
                existing_sections.extend(prompt_sections)
                self.toolkit.prompt_sections = tuple(existing_sections)
            for tool_obj in entry.tools.values():
                self.toolkit.register(tool_obj)
            return
        if isinstance(entry, Tool):
            self._ensure_unique_tool_names([entry.name], source_label=f"tool {entry.name}")
            self.toolkit.register(entry)
            return
        if callable(entry):
            tool_name = getattr(entry, "__name__", "").strip()
            if tool_name:
                self._ensure_unique_tool_names([tool_name], source_label=f"callable {tool_name}")
            self.toolkit.register(entry)
            return
        raise TypeError(f"unsupported tool entry: {type(entry).__name__}")

    def add_harness(self, harness: Any) -> None:
        self.harnesses.append(harness)

    def attach_memory_runtime(
        self,
        memory_runtime: KernelMemoryRuntime,
        *,
        component_mode: MemoryRuntimeComponentMode = (
            MemoryRuntimeComponentMode.FULL
        ),
    ) -> None:
        if not isinstance(memory_runtime, KernelMemoryRuntime):
            raise TypeError("memory_runtime must be a KernelMemoryRuntime")
        if not isinstance(component_mode, MemoryRuntimeComponentMode):
            raise TypeError("component_mode must be a MemoryRuntimeComponentMode")
        if self.memory_runtime is not None and (
            self.memory_runtime is not memory_runtime
            or self.memory_runtime_component_mode is not component_mode
        ):
            raise ValueError("memory runtime is already attached with another mode")
        self.memory_runtime = memory_runtime
        self.memory_runtime_component_mode = component_mode

    def attach_context_runtime(self, context_runtime: "ContextRuntime") -> None:
        from ..context import ContextRuntime

        if not isinstance(context_runtime, ContextRuntime):
            raise TypeError("context_runtime must be a ContextRuntime")
        incoming_owner = context_runtime.owner_id
        if self.semantic_context_owner is not None:
            raise ValueError(
                "semantic context owner is already claimed by "
                f"{self.semantic_context_owner!r}; cannot attach {incoming_owner!r}"
            )
        self.semantic_context_owner = incoming_owner
        self.context_runtime = context_runtime
        for harness in context_runtime.build_harnesses():
            self.add_harness(harness)

    def set_model_io(self, model_io: ModelIO) -> None:
        self._model_io = model_io

    def set_model_io_factory(self, factory: Callable[[AgentSpec, AgentCallContext], ModelIO]) -> None:
        self._model_io_factory = factory

    def add_run_hook(self, hook: RunHook) -> None:
        self.run_hooks.append(hook)

    def add_tool_runtime_plugin(self, plugin: Any) -> None:
        self.tool_runtime_plugins.append(plugin)

    def set_tool_optimizer_config(self, config: ToolOptimizerConfig | dict[str, Any] | None) -> None:
        self.tool_optimizer_config = ToolOptimizerConfig.coerce(config)

    def set_completion_policy(self, policy: CompletionPolicy | None) -> None:
        self.completion_policy = policy

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
            if self.spec.missing_tool_policy == "raise":
                raise ValueError(
                    f"agent {self.spec.name!r} allowed_tools contains unknown tool names: {', '.join(missing)}"
                )
            logger.warning(
                "agent %r allowed_tools contains unknown tool names (skipped): %s",
                self.spec.name,
                ", ".join(missing),
            )
            allowed_names = [name for name in allowed_names if name not in missing]
        allowed_name_set = set(allowed_names)
        self.toolkit.tools = {
            name: self.toolkit.tools[name]
            for name in configured_names
            if name in allowed_name_set
        }

    def build(self) -> PreparedAgent:
        self._apply_allowed_tools_filter()
        if (self.semantic_context_owner is None) != (self.context_runtime is None):
            raise ValueError(
                "semantic context owner and context runtime must be configured together"
            )
        loop = build_runtime_loop(
            harnesses=self.harnesses,
            model_io=self._resolve_model_io(),
            memory_runtime=self.memory_runtime,
            semantic_context_owner=self.semantic_context_owner,
            memory_runtime_component_mode=self.memory_runtime_component_mode,
        )
        prepared_call_context = self.call_context
        if self.context_runtime is not None:
            prepared_call_context = replace(
                self.call_context,
                callback=self.context_runtime.compose_event_callback(
                    self.call_context.callback
                ),
            )
        return PreparedAgent(
            loop=loop,
            toolkit=self.toolkit,
            spec=self.spec,
            state=self.state,
            call_context=prepared_call_context,
            memory_runtime=self.memory_runtime,
            memory_runtime_component_mode=self.memory_runtime_component_mode,
            default_payload=copy.deepcopy(self.default_payload),
            default_response_format=self.default_response_format,
            default_max_iterations=self.default_max_iterations,
            default_max_context_window_tokens=self.default_max_context_window_tokens,
            default_on_tool_confirm=self.default_on_tool_confirm,
            default_on_human_input=self.default_on_human_input,
            default_on_max_iterations=self.default_on_max_iterations,
            run_hooks=list(self.run_hooks),
            tool_runtime_plugins=list(self.tool_runtime_plugins),
            tool_optimizer_config=self.tool_optimizer_config,
            completion_policy=self.completion_policy,
            session_history_owned_by_memory=(
                self.memory_runtime is not None
                and self.memory_runtime_component_mode
                is MemoryRuntimeComponentMode.FULL
                and bool(self.call_context.session_id)
            ),
            semantic_context_owner=self.semantic_context_owner,
            context_runtime=self.context_runtime,
        )
