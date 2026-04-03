from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .delta import (
    ActivateVersionOp,
    AppendMessagesOp,
    DeleteSpanOp,
    HarnessDelta,
    InsertMessagesOp,
    ReplaceSpanOp,
)
from ..subagents.types import SubagentState
from ..tools.types import ToolBatchState
from .types import ModelTurnResult, ToolCall
from .versioning import MessageVersionGraph


def _deepcopy_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for message in (messages or []) if isinstance(message, dict)]


@dataclass
class TokenState:
    consumed_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    last_turn_tokens: int = 0
    last_turn_input_tokens: int = 0
    last_turn_output_tokens: int = 0
    last_turn_cache_read_input_tokens: int = 0
    last_turn_cache_creation_input_tokens: int = 0


@dataclass
class ProviderState:
    provider: str | None = None
    model: str | None = None
    previous_response_id: str | None = None
    use_previous_response_chain: bool = False
    max_context_window_tokens: int = 0


@dataclass
class SessionState:
    session_id: str | None = None
    memory_namespace: str | None = None


@dataclass
class SuspendState:
    signal_kind: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunState:
    """Top-level mutable state for one kernel loop run."""

    transcript: list[dict[str, Any]] = field(default_factory=list)
    versions: MessageVersionGraph = field(default_factory=MessageVersionGraph)
    latest_version_id: str | None = None
    iteration: int = 0
    provider_state: ProviderState = field(default_factory=ProviderState)
    token_state: TokenState = field(default_factory=TokenState)
    session_state: SessionState = field(default_factory=SessionState)
    suspend_state: SuspendState = field(default_factory=SuspendState)
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    last_model_turn: ModelTurnResult | None = None
    tool_batch_state: ToolBatchState = field(default_factory=ToolBatchState)
    run_status: str = "idle"
    last_continuation: dict[str, Any] | None = None
    next_model_input: list[dict[str, Any]] | None = None
    memory_state: dict[str, Any] = field(default_factory=dict)
    memory_prepare_info: dict[str, Any] = field(default_factory=dict)
    memory_commit_info: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    optimizer_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    subagent_state: SubagentState = field(default_factory=SubagentState)
    component_state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._sync_all_component_state()

    def component_bucket(self, name: str) -> dict[str, Any]:
        bucket = self.component_state.get(name)
        if not isinstance(bucket, dict):
            bucket = {}
            self.component_state[name] = bucket
        return bucket

    def _sync_all_component_state(self) -> None:
        self.component_state["optimizers"] = copy.deepcopy(self.optimizer_state)
        self.component_bucket("memory").update(
            {
                "state": copy.deepcopy(self.memory_state),
                "prepare_info": copy.deepcopy(self.memory_prepare_info),
                "commit_info": copy.deepcopy(self.memory_commit_info),
            }
        )
        self.component_bucket("tools")["tool_batch_state"] = self.tool_batch_state.copy()
        self.component_bucket("subagents")["state"] = self.subagent_state.copy()

    def seed_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        created_by: str = "seed",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        version = self.versions.create_version(
            messages=messages,
            parent_version_id=None,
            created_by=created_by,
            metadata=metadata,
        )
        self.latest_version_id = version.version_id
        self.transcript = _deepcopy_messages(messages)
        return version.version_id

    def latest_messages(self) -> list[dict[str, Any]]:
        if self.latest_version_id is None:
            return []
        return self.versions.get_messages(self.latest_version_id)

    def rebuild_working_version_from_transcript(
        self,
        *,
        created_by: str = "kernel.transcript_snapshot",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        version = self.versions.create_version(
            messages=self.transcript,
            parent_version_id=self.latest_version_id,
            created_by=created_by,
            metadata=metadata,
        )
        self.latest_version_id = version.version_id
        return version.version_id

    def view_messages(self, version_id: str | None = None) -> list[dict[str, Any]]:
        resolved_version_id = version_id or self.latest_version_id
        if resolved_version_id is None:
            return []
        return self.versions.get_messages(resolved_version_id)

    def apply_delta(self, delta: HarnessDelta) -> str | None:
        requested_base_version_id = delta.base_version_id or self.latest_version_id
        if requested_base_version_id is None and delta.ops:
            raise ValueError("cannot apply a message delta before seeding a base version")

        applied_base_version_id = (
            self.latest_version_id
            if delta.rebase_to_latest and self.latest_version_id is not None
            else requested_base_version_id
        )
        working_messages = self.view_messages(applied_base_version_id)

        for op in delta.ops:
            if isinstance(op, ActivateVersionOp):
                self.versions.activate(op.version_id)
                self.latest_version_id = op.version_id
                working_messages = self.view_messages(op.version_id)
                continue
            if isinstance(op, AppendMessagesOp):
                working_messages.extend(_deepcopy_messages(op.messages))
                continue
            if isinstance(op, InsertMessagesOp):
                index = max(0, min(int(op.index), len(working_messages)))
                working_messages[index:index] = _deepcopy_messages(op.messages)
                continue
            if isinstance(op, ReplaceSpanOp):
                start = max(0, min(int(op.start), len(working_messages)))
                end = max(start, min(int(op.end), len(working_messages)))
                working_messages[start:end] = _deepcopy_messages(op.messages)
                continue
            if isinstance(op, DeleteSpanOp):
                start = max(0, min(int(op.start), len(working_messages)))
                end = max(start, min(int(op.end), len(working_messages)))
                del working_messages[start:end]
                continue
            raise TypeError(f"unsupported message op: {type(op).__name__}")

        version_id = self.latest_version_id
        non_activate_ops = tuple(type(op).__name__ for op in delta.ops if not isinstance(op, ActivateVersionOp))
        if non_activate_ops:
            version = self.versions.create_version(
                messages=working_messages,
                parent_version_id=applied_base_version_id,
                created_by=delta.created_by,
                metadata={
                    "requested_base_version_id": requested_base_version_id,
                    "applied_base_version_id": applied_base_version_id,
                    "op_types": list(non_activate_ops),
                    "trace": copy.deepcopy(delta.trace),
                },
            )
            self.latest_version_id = version.version_id
            version_id = version.version_id

        self._apply_state_updates(delta.state_updates)
        if delta.suspend is not None:
            self.suspend_state.signal_kind = delta.suspend.kind
            self.suspend_state.payload = copy.deepcopy(delta.suspend.payload)
        return version_id

    def _apply_state_updates(self, updates: dict[str, Any]) -> None:
        for key, value in (updates or {}).items():
            if key == "iteration":
                self.iteration = int(value)
                continue
            if key == "transcript":
                self.transcript = _deepcopy_messages(value if isinstance(value, list) else [])
                continue
            if key == "transcript_append":
                self.transcript.extend(_deepcopy_messages(value if isinstance(value, list) else []))
                continue
            if key == "artifacts":
                self.artifacts = copy.deepcopy(value) if isinstance(value, list) else []
                continue
            if key == "optimizer_state" and isinstance(value, dict):
                for optimizer_name, optimizer_value in value.items():
                    if not isinstance(optimizer_name, str):
                        continue
                    if isinstance(optimizer_value, dict):
                        self.optimizer_state[optimizer_name] = copy.deepcopy(optimizer_value)
                    else:
                        self.optimizer_state[optimizer_name] = {"value": copy.deepcopy(optimizer_value)}
                self.component_state["optimizers"] = copy.deepcopy(self.optimizer_state)
                continue
            if key == "subagent_state":
                if isinstance(value, SubagentState):
                    self.subagent_state = value.copy()
                else:
                    self.subagent_state = self.subagent_state.merged(value)
                self.component_bucket("subagents")["state"] = self.subagent_state.copy()
                continue
            if key == "memory_state" and isinstance(value, dict):
                self.memory_state.update(copy.deepcopy(value))
                self.component_bucket("memory")["state"] = copy.deepcopy(self.memory_state)
                continue
            if key == "memory_prepare_info" and isinstance(value, dict):
                self.memory_prepare_info.update(copy.deepcopy(value))
                self.component_bucket("memory")["prepare_info"] = copy.deepcopy(self.memory_prepare_info)
                continue
            if key == "memory_commit_info" and isinstance(value, dict):
                self.memory_commit_info.update(copy.deepcopy(value))
                self.component_bucket("memory")["commit_info"] = copy.deepcopy(self.memory_commit_info)
                continue
            if key == "pending_tool_calls":
                if isinstance(value, list):
                    self.pending_tool_calls = [
                        item if isinstance(item, ToolCall) else copy.deepcopy(item)
                        for item in value
                    ]
                else:
                    self.pending_tool_calls = []
                continue
            if key == "last_model_turn":
                self.last_model_turn = (
                    value
                    if isinstance(value, ModelTurnResult) or value is None
                    else copy.deepcopy(value)
                )
                continue
            if key == "tool_batch_state":
                if isinstance(value, ToolBatchState):
                    self.tool_batch_state = value.copy()
                elif isinstance(value, dict):
                    current = self.tool_batch_state.copy()
                    if "result_messages" in value:
                        current.result_messages = _deepcopy_messages(value.get("result_messages"))
                    if "should_observe" in value:
                        current.should_observe = bool(value.get("should_observe"))
                    if "awaiting_human_input" in value:
                        current.awaiting_human_input = bool(value.get("awaiting_human_input"))
                    if "human_input_request" in value:
                        current.human_input_request = copy.deepcopy(value.get("human_input_request"))
                    if "human_input_tool_call_id" in value:
                        current.human_input_tool_call_id = copy.deepcopy(value.get("human_input_tool_call_id"))
                    if "executed_call_ids" in value:
                        current.executed_call_ids = [
                            str(item)
                            for item in (value.get("executed_call_ids") or [])
                            if isinstance(item, str)
                        ]
                    self.tool_batch_state = current
                else:
                    self.tool_batch_state = ToolBatchState()
                self.component_bucket("tools")["tool_batch_state"] = self.tool_batch_state.copy()
                continue
            if key == "run_status":
                self.run_status = str(value or "idle")
                continue
            if key == "last_continuation":
                self.last_continuation = copy.deepcopy(value) if isinstance(value, dict) else None
                continue
            if key == "next_model_input":
                self.next_model_input = _deepcopy_messages(value if isinstance(value, list) else [])
                if not self.next_model_input:
                    self.next_model_input = None
                continue
            if key == "provider_state" and isinstance(value, dict):
                for inner_key, inner_value in value.items():
                    if hasattr(self.provider_state, inner_key):
                        setattr(self.provider_state, inner_key, copy.deepcopy(inner_value))
                continue
            if key == "token_state" and isinstance(value, dict):
                for inner_key, inner_value in value.items():
                    if hasattr(self.token_state, inner_key):
                        setattr(self.token_state, inner_key, int(inner_value))
                continue
            if key == "session_state" and isinstance(value, dict):
                for inner_key, inner_value in value.items():
                    if hasattr(self.session_state, inner_key):
                        setattr(self.session_state, inner_key, copy.deepcopy(inner_value))
                continue
            if key == "suspend_state" and isinstance(value, dict):
                for inner_key, inner_value in value.items():
                    if hasattr(self.suspend_state, inner_key):
                        setattr(self.suspend_state, inner_key, copy.deepcopy(inner_value))
                continue
            self.metadata[key] = copy.deepcopy(value)
