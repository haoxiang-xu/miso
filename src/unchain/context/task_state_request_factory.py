"""Additive Pinned Task State injection for canonical context requests.

The decorator deliberately builds the base request first.  Only after that
immutable journal-derived request exists does it consult the independently
bound task-state reader and inject either bounded content or a content-free
unavailable marker.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from unchain.journal import ContextBuildStatus
from unchain.journal.models import _required_text
from unchain.kernel.harness import HarnessContext

from .models import ContextCompileRequest
from .ports import BoundContextTaskStateReader
from .runtime import ContextRequestFactory
from .task_state import ContextTaskStateReadOutcome


class TaskStateContextRequestFactoryError(RuntimeError):
    """A bound task-state read could not safely decorate a context request."""


@dataclass(frozen=True, slots=True)
class TaskStateContextRequestFactory:
    """Compose any request factory with one immutable task-state binding."""

    binding_id: str
    base_factory: ContextRequestFactory
    reader: BoundContextTaskStateReader

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "binding_id",
            _required_text(
                self.binding_id,
                "binding_id",
                maximum=512,
                identifier=True,
            ),
        )
        if not callable(self.base_factory):
            raise TypeError("base_factory must be a ContextRequestFactory")
        if not isinstance(self.reader, BoundContextTaskStateReader):
            raise TypeError(
                "reader must be a BoundContextTaskStateReader"
            )
        if self.reader.binding_id != self.binding_id:
            raise TaskStateContextRequestFactoryError(
                "task-state reader belongs to another binding"
            )

    @property
    def attempt(self) -> Any:
        """Preserve an attempt identity exposed by the composed base factory."""

        return self.base_factory.attempt

    @property
    def journal(self) -> Any:
        """Preserve a journal identity exposed by the composed base factory."""

        return self.base_factory.journal

    @staticmethod
    def _capture_quality(
        request: ContextCompileRequest,
        outcome: ContextTaskStateReadOutcome,
    ) -> str:
        if outcome.capture_quality is ContextBuildStatus.UNAVAILABLE:
            return ContextBuildStatus.UNAVAILABLE.value
        if request.capture_quality is None:
            return ContextBuildStatus.COMPLETE.value
        return ContextBuildStatus(request.capture_quality).value

    def __call__(self, context: HarnessContext) -> ContextCompileRequest:
        request = self.base_factory(context)
        if not isinstance(request, ContextCompileRequest):
            raise TaskStateContextRequestFactoryError(
                "base factory returned an invalid context request"
            )
        if request.task_state is not None or request.task_state_unavailable is not None:
            raise TaskStateContextRequestFactoryError(
                "base request already owns task-state projection"
            )
        try:
            outcome = self.reader.read_for_context()
        except Exception as error:
            raise TaskStateContextRequestFactoryError(
                "task-state read failed closed"
            ) from error
        if not isinstance(outcome, ContextTaskStateReadOutcome):
            raise TaskStateContextRequestFactoryError(
                "task-state reader returned an invalid outcome"
            )
        return replace(
            request,
            task_state=(
                outcome.state.to_dict()
                if outcome.state is not None
                else None
            ),
            task_state_unavailable=outcome.unavailable,
            capture_quality=self._capture_quality(request, outcome),
        )


__all__ = [
    "TaskStateContextRequestFactory",
    "TaskStateContextRequestFactoryError",
]
