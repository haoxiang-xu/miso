from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ...schemas import ResponseFormat
from .base import BaseAgentModule


@dataclass(frozen=True)
class PoliciesModule(BaseAgentModule):
    payload: dict[str, Any] = field(default_factory=dict)
    response_format: ResponseFormat | None = None
    max_iterations: int | None = None
    max_context_window_tokens: int | None = None
    name: str = "policies"

    def configure(self, builder) -> None:
        builder.set_payload_defaults(copy.deepcopy(self.payload))
        if self.response_format is not None:
            builder.set_response_format_default(self.response_format)
        if self.max_iterations is not None:
            builder.set_max_iterations_default(int(self.max_iterations))
        if self.max_context_window_tokens is not None:
            builder.set_max_context_window_tokens_default(int(self.max_context_window_tokens))
