from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass, field

from ..capabilities import ContextTarget, EmitEventOp, InsertMessagesOp, RunDelta
from ..kernel.harness import BaseRuntimeHarness, HarnessContext
from ..kernel.provider_replay import current_provider_replay_frame


@dataclass(frozen=True)
class FyiMessage:
    text: str
    origin: str = "user"
    message_id: str = field(default_factory=lambda: "fyi_" + uuid.uuid4().hex)


class FyiChannel:
    """Thread-safe mid-run message channel.

    The caller keeps a reference and may ``post`` from any thread while the
    agent loop drains pending messages at each ``before_model`` boundary.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[FyiMessage] = []

    def post(self, text: str, *, origin: str = "user") -> str:
        message = FyiMessage(text=text, origin=origin)
        with self._lock:
            self._pending.append(message)
        return message.message_id

    def drain(self) -> list[FyiMessage]:
        with self._lock:
            drained = self._pending
            self._pending = []
        return drained

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


FYI_CREATED_BY = "interaction.fyi"

_USER_TEMPLATE = (
    "<fyi_message>\n"
    "The user sent a new message while you were working on the current task:\n"
    "{text}\n\n"
    "If this is just a question, answer it briefly at the start of your next "
    "response, then continue the task. If it changes the requirements, "
    "incorporate it into your plan. Do not ignore this message.\n"
    "</fyi_message>"
)

_SYSTEM_TEMPLATE = (
    "<fyi_message>\n"
    "While you were working, the user asked a side question and a "
    "side assistant already replied:\n"
    "{text}\n\n"
    "No action needed unless it affects your plan. Do not ignore this message.\n"
    "</fyi_message>"
)


def wrap_fyi(message: FyiMessage) -> dict:
    template = _SYSTEM_TEMPLATE if message.origin == "system" else _USER_TEMPLATE
    return {"role": "user", "content": template.format(text=message.text)}


@dataclass
class FyiInjectionHarness(BaseRuntimeHarness):
    name: str = "fyi_injection"
    phases: tuple[str, ...] = ("before_model",)
    order: int = 180
    channel: FyiChannel | None = None

    def build_delta(self, context: HarnessContext):
        if self.channel is None:
            return None
        drained = self.channel.drain()
        if not drained:
            return None
        wrapped = [wrap_fyi(m) for m in drained]
        state_updates = {}
        if isinstance(current_provider_replay_frame(context.state), dict):
            state_updates["provider_replay_append"] = copy.deepcopy(wrapped)
        if (
            context.state.provider_state.use_previous_response_chain
            and isinstance(context.state.next_model_input, list)
        ):
            state_updates["next_model_input"] = [
                *copy.deepcopy(context.state.next_model_input),
                *copy.deepcopy(wrapped),
            ]
        # Append onto the CONVERSATION target: apply_run_delta both extends
        # state.transcript (persistence into the final result) AND creates a
        # new version graph node from it, becoming state.latest_version_id —
        # so the injected message is visible to the model in *this* turn's
        # state.latest_messages(), not just on the next iteration's rebuild.
        # The state updates also preserve both provider context owners: a
        # stateful continuation gets the FYI in its next input delta, while a
        # manual/cold continuation gets it in the lossless provider replay.
        return RunDelta(
            created_by=FYI_CREATED_BY,
            context_ops=(
                InsertMessagesOp(
                    target=ContextTarget.CONVERSATION,
                    index=len(context.state.transcript),
                    messages=wrapped,
                    reason=FYI_CREATED_BY,
                ),
                EmitEventOp(
                    type="fyi_injected",
                    payload={
                        "count": len(drained),
                        "messages": [
                            {"message_id": m.message_id, "origin": m.origin, "text": m.text}
                            for m in drained
                        ],
                    },
                    reason=FYI_CREATED_BY,
                ),
            ),
            state_updates=state_updates,
            trace={"fyi_injected_count": len(drained)},
        )
