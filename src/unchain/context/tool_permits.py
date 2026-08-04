from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..kernel.harness import HarnessContext
from .tool_executor import (
    DurableToolExecutionRequest,
    DurableToolExecutor,
    DurableToolInvocation,
)


MAX_PREPARED_TOOL_PERMITS = 1_024
_DURABLE_TOOL_APPROVAL_PENDING_AUTHORITY = object()


@dataclass(frozen=True)
class DurableToolApprovalPending:
    """Public, immutable view of a Runtime-owned approval suspension."""

    _interaction_request: dict[str, Any] = field(repr=False, compare=False)
    _continuation: dict[str, Any] = field(repr=False, compare=False)
    _authority: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        from ..interaction.durable import InteractionRequest
        if self._authority is not _DURABLE_TOOL_APPROVAL_PENDING_AUTHORITY:
            raise TypeError(
                "durable tool approval pending records are runtime-owned"
            )
        request = InteractionRequest.from_dict(self._interaction_request)
        try:
            continuation = json.loads(
                json.dumps(
                    self._continuation,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "tool approval continuation must be strict JSON"
            ) from exc
        if not isinstance(continuation, dict):
            raise TypeError("tool approval continuation must be an object")
        object.__setattr__(self, "_interaction_request", request.to_dict())
        object.__setattr__(self, "_continuation", continuation)

    @property
    def interaction_request(self) -> dict[str, Any]:
        from copy import deepcopy

        return deepcopy(self._interaction_request)

    @property
    def continuation(self) -> dict[str, Any]:
        from copy import deepcopy

        return deepcopy(self._continuation)


def _new_tool_approval_pending(
    *,
    interaction_request: dict[str, Any],
    continuation: dict[str, Any],
) -> DurableToolApprovalPending:
    return DurableToolApprovalPending(
        _interaction_request=interaction_request,
        _continuation=continuation,
        _authority=_DURABLE_TOOL_APPROVAL_PENDING_AUTHORITY,
    )


class _DurableToolPermit:
    """Opaque, runtime-owned permission to consume one prepared invocation."""

    __slots__ = ("__authority", "__nonce")

    def __init__(self, *, authority: object) -> None:
        self.__authority = authority
        self.__nonce = object()

    def __repr__(self) -> str:
        return "<DurableToolPermit opaque>"

    def __reduce__(self):
        raise TypeError("durable tool permits cannot be serialized")

    def __reduce_ex__(self, protocol):
        del protocol
        raise TypeError("durable tool permits cannot be serialized")

    def _belongs_to(self, authority: object) -> bool:
        return self.__authority is authority


@dataclass(frozen=True)
class _PreparedToolBinding:
    permit: _DurableToolPermit = field(repr=False, compare=False)
    context: HarnessContext = field(repr=False, compare=False)
    attempt_key: tuple[str, str]
    call_id: str
    request: DurableToolExecutionRequest = field(repr=False, compare=False)
    executor: DurableToolExecutor = field(repr=False, compare=False)
    invocation: DurableToolInvocation | None = field(repr=False, compare=False)
    route: Any = field(repr=False, compare=False)


def _mint_tool_permit(authority: object) -> _DurableToolPermit:
    return _DurableToolPermit(authority=authority)


def _is_runtime_tool_permit(value: object, authority: object) -> bool:
    return (
        type(value) is _DurableToolPermit
        and value._belongs_to(authority)
    )


__all__ = [
    "DurableToolApprovalPending",
    "MAX_PREPARED_TOOL_PERMITS",
]
