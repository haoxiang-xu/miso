"""Sanctioned runtime seam for Pinned Task State request decoration.

The durable factory continues to build and validate an unchanged
``ContextExecutionBundle`` with its exact ``JournalContextRequestFactory``.
After that boundary succeeds, this runtime creates an attempt-local official
``ContextRuntime`` whose only variation is the additive task-state request
decorator.  No bundle field is replaced or mutated.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Protocol

from unchain.journal import AttemptRef
from unchain.journal.models import _required_text
from unchain.kernel.harness import HarnessContext
from unchain.subagents.types import SubagentResult

from .factory import ContextExecutionBundle, DurableContextRuntimeFactory
from .ports import BoundContextTaskStateReader
from .runtime import ContextRequestFactory, ContextRuntime
from .task_state_request_factory import TaskStateContextRequestFactory


class TaskStateReaderResolver(Protocol):
    def __call__(
        self,
        bundle: ContextExecutionBundle,
    ) -> BoundContextTaskStateReader: ...


class TaskStateContextRuntimeError(RuntimeError):
    """The additive compile runtime could not bind to a verified bundle."""


@dataclass(frozen=True)
class TaskStateContextSubagentCompletionSink:
    """Subagent completion sink compatible with the sanctioned runtime subtype."""

    _runtime: TaskStateContextRuntime = field(repr=False, compare=False)
    parent_attempt: AttemptRef
    call_id: str

    def __post_init__(self) -> None:
        if not isinstance(self._runtime, TaskStateContextRuntime):
            raise TypeError(
                "task-state subagent completion sink requires its bound runtime"
            )
        if not isinstance(self.parent_attempt, AttemptRef):
            object.__setattr__(
                self,
                "parent_attempt",
                AttemptRef.from_dict(self.parent_attempt),
            )
        object.__setattr__(
            self,
            "call_id",
            _required_text(self.call_id, "call_id", identifier=True),
        )

    def record(self, *, child_run_id: str, result: SubagentResult):
        if type(result) is not SubagentResult:
            raise TypeError("subagent completion requires an exact SubagentResult")
        return self._runtime._record_subagent_completion(
            parent_attempt=self.parent_attempt,
            call_id=self.call_id,
            child_run_id=child_run_id,
            result=result,
        )


@dataclass(frozen=True)
class TaskStateContextRuntime(ContextRuntime):
    """Factory ContextRuntime with attempt-local task-state compile runtimes."""

    task_state_reader_resolver: TaskStateReaderResolver = field(
        kw_only=True,
        repr=False,
        compare=False,
    )
    _task_state_runtime_lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
        compare=False,
    )
    _task_state_compile_runtimes: dict[tuple[str, str], ContextRuntime] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.execution_factory is None:
            raise TypeError(
                "task-state context runtime requires a durable execution factory"
            )
        if not callable(self.task_state_reader_resolver):
            raise TypeError("task_state_reader_resolver must be callable")

    @classmethod
    def from_factory(
        cls,
        *,
        owner_id: str,
        execution_factory: DurableContextRuntimeFactory,
        task_state_reader_resolver: TaskStateReaderResolver,
        provider_turns_enabled: bool = False,
        tool_output_management_active: bool = False,
    ) -> TaskStateContextRuntime:
        return cls(
            owner_id=owner_id,
            execution_factory=execution_factory,
            provider_turns_enabled=provider_turns_enabled,
            tool_output_management_active=tool_output_management_active,
            task_state_reader_resolver=task_state_reader_resolver,
        )

    @staticmethod
    def _bundle_key(bundle: ContextExecutionBundle) -> tuple[str, str]:
        return (
            bundle.attempt.generation.execution_id,
            bundle.attempt.attempt_id,
        )

    def _resolve_reader(
        self,
        bundle: ContextExecutionBundle,
    ) -> BoundContextTaskStateReader:
        try:
            reader = self.task_state_reader_resolver(bundle)
        except Exception as error:
            raise TaskStateContextRuntimeError(
                "task-state reader resolution failed closed"
            ) from error
        if not isinstance(reader, BoundContextTaskStateReader):
            raise TaskStateContextRuntimeError(
                "task-state reader resolver returned an invalid capability"
            )
        return reader

    def _compile_runtime(
        self,
        *,
        bundle: ContextExecutionBundle,
        reader: BoundContextTaskStateReader,
    ) -> ContextRuntime:
        request_factory: ContextRequestFactory = TaskStateContextRequestFactory(
            binding_id=reader.binding_id,
            base_factory=bundle.request_factory,
            reader=reader,
        )
        identity = "\0".join(
            (
                self.owner_id,
                bundle.attempt.generation.execution_id,
                bundle.attempt.attempt_id,
                reader.binding_id,
            )
        )
        owner_id = "task-state-context-" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()
        return ContextRuntime(
            owner_id=owner_id,
            request_factory=request_factory,
            durable_event_sink=bundle.durable_event_sink,
            partial_attempt_sink=bundle.partial_attempt_sink,
            compiler=bundle.coordinator,
        )

    def bind_context(
        self,
        context: HarnessContext,
        *,
        _binding_authority: object | None = None,
        _shadow_mode: bool = False,
    ) -> None:
        super().bind_context(
            context,
            _binding_authority=_binding_authority,
            _shadow_mode=_shadow_mode,
        )
        factory = self.execution_factory
        if factory is None:  # pragma: no cover - guarded by construction
            raise TaskStateContextRuntimeError(
                "durable execution factory disappeared"
            )
        bundle = factory.bind(context)
        reader = self._resolve_reader(bundle)
        key = self._bundle_key(bundle)
        with self._task_state_runtime_lock:
            existing = self._task_state_compile_runtimes.get(key)
            if existing is not None:
                request_factory = existing.request_factory
                if (
                    not isinstance(
                        request_factory,
                        TaskStateContextRequestFactory,
                    )
                    or request_factory.base_factory is not bundle.request_factory
                    or request_factory.reader.binding_id != reader.binding_id
                ):
                    raise TaskStateContextRuntimeError(
                        "task-state compile binding changed"
                    )
                return
            self._task_state_compile_runtimes[key] = self._compile_runtime(
                bundle=bundle,
                reader=reader,
            )

    def compile_context(self, context: HarnessContext):
        bundle = self._bundle_for_context(context)
        key = self._bundle_key(bundle)
        self._raise_latched_failure(key)
        with self._task_state_runtime_lock:
            runtime = self._task_state_compile_runtimes.get(key)
        if runtime is None:
            raise TaskStateContextRuntimeError(
                "task-state compile occurred before bootstrap binding"
            )
        return runtime.compile_context(context)

    def prepare_subagent_completion_sink(
        self,
        context: HarnessContext,
        *,
        call_id: str,
    ) -> TaskStateContextSubagentCompletionSink | None:
        if self.execution_factory is None:
            return None
        if not isinstance(context, HarnessContext):
            raise TypeError("subagent completion binding requires HarnessContext")
        bundle = self._bundle_for_context(context)
        key = self._bundle_key(bundle)
        self._raise_latched_failure(key)
        return TaskStateContextSubagentCompletionSink(
            _runtime=self,
            parent_attempt=bundle.attempt,
            call_id=call_id,
        )


__all__ = [
    "TaskStateContextRuntime",
    "TaskStateContextRuntimeError",
    "TaskStateContextSubagentCompletionSink",
    "TaskStateReaderResolver",
]
