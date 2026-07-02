from __future__ import annotations

import copy
from typing import Any

from ..capabilities import EmitEventOp, RequestSuspendOp
from .human_input import HumanInputRequest


INTERACTION_EFFECT_CREATED_BY = "interaction.human_input"


def build_human_input_requested_event(request: HumanInputRequest) -> EmitEventOp:
    return EmitEventOp(
        type="human_input_requested",
        payload={
            "request_id": request.request_id,
            "kind": request.kind,
            "title": request.title,
            "question": request.question,
            "selection_mode": request.selection_mode,
            "options": [option.to_dict() for option in request.options],
            "allow_other": request.allow_other,
            "other_label": request.other_label,
            "other_placeholder": request.other_placeholder,
            "min_selected": request.min_selected,
            "max_selected": request.max_selected,
        },
        reason="interaction.requested",
    )


def build_human_input_suspend_request(
    request: HumanInputRequest,
    *,
    continuation: dict[str, Any],
) -> RequestSuspendOp:
    return RequestSuspendOp(
        kind="human_input",
        payload={
            "continuation": copy.deepcopy(continuation),
            "request": request.to_dict(),
        },
        reason="interaction.awaiting_human_input",
    )


__all__ = [
    "INTERACTION_EFFECT_CREATED_BY",
    "build_human_input_requested_event",
    "build_human_input_suspend_request",
]
