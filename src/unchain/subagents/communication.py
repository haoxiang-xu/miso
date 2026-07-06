from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from typing import Any

from .types import SubagentPolicy, SubagentState


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(str(item) for item in value if isinstance(item, str))
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, str))
    return ()


@dataclass(frozen=True)
class AgentThreadRecord:
    thread_id: str
    agent_id: str
    parent_agent_id: str
    target: str
    template_name: str | None
    mode: str
    status: str
    session_id: str
    memory_namespace: str
    lineage: tuple[str, ...]
    created_iteration: int
    last_activity_iteration: int
    context_mode: str
    instructions: str = ""
    expected_output: str = ""
    close_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "target": self.target,
            "template_name": self.template_name,
            "mode": self.mode,
            "status": self.status,
            "session_id": self.session_id,
            "memory_namespace": self.memory_namespace,
            "lineage": list(self.lineage),
            "created_iteration": int(self.created_iteration),
            "last_activity_iteration": int(self.last_activity_iteration),
            "context_mode": self.context_mode,
            "instructions": self.instructions,
            "expected_output": self.expected_output,
            "close_reason": self.close_reason,
        }

    @classmethod
    def from_raw(cls, raw: Any) -> "AgentThreadRecord":
        if not isinstance(raw, dict):
            raise TypeError("agent thread record must be a dict")
        return cls(
            thread_id=str(raw.get("thread_id") or ""),
            agent_id=str(raw.get("agent_id") or ""),
            parent_agent_id=str(raw.get("parent_agent_id") or ""),
            target=str(raw.get("target") or ""),
            template_name=str(raw["template_name"]) if raw.get("template_name") is not None else None,
            mode=str(raw.get("mode") or "thread"),
            status=str(raw.get("status") or "pending"),
            session_id=str(raw.get("session_id") or ""),
            memory_namespace=str(raw.get("memory_namespace") or ""),
            lineage=_string_tuple(raw.get("lineage")),
            created_iteration=int(raw.get("created_iteration") or 0),
            last_activity_iteration=int(raw.get("last_activity_iteration") or 0),
            context_mode=str(raw.get("context_mode") or "none"),
            instructions=str(raw.get("instructions") or ""),
            expected_output=str(raw.get("expected_output") or ""),
            close_reason=str(raw.get("close_reason") or ""),
        )


@dataclass(frozen=True)
class AgentMessage:
    message_id: str
    sender_agent_id: str
    recipient_agent_id: str
    thread_id: str
    kind: str
    content: str
    created_iteration: int
    correlation_id: str | None = None
    reply_to_message_id: str | None = None
    requires_ack: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender_agent_id": self.sender_agent_id,
            "recipient_agent_id": self.recipient_agent_id,
            "thread_id": self.thread_id,
            "kind": self.kind,
            "content": self.content,
            "created_iteration": int(self.created_iteration),
            "correlation_id": self.correlation_id,
            "reply_to_message_id": self.reply_to_message_id,
            "requires_ack": bool(self.requires_ack),
        }


@dataclass(frozen=True)
class BlackboardItem:
    item_id: str
    board_id: str
    author_agent_id: str
    kind: str
    title: str
    content: str
    tags: tuple[str, ...]
    confidence: float | None
    refs: tuple[str, ...]
    created_iteration: int
    supersedes_item_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "board_id": self.board_id,
            "author_agent_id": self.author_agent_id,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "tags": list(self.tags),
            "confidence": self.confidence,
            "refs": list(self.refs),
            "created_iteration": int(self.created_iteration),
            "supersedes_item_id": self.supersedes_item_id,
        }


@dataclass(frozen=True)
class AgentCommunicationRuntime:
    policy: SubagentPolicy

    def upsert_thread(self, state: SubagentState, record: AgentThreadRecord) -> SubagentState:
        current = state.copy()
        open_count = sum(
            1
            for item in current.threads.values()
            if isinstance(item, dict) and item.get("status") not in {"completed", "failed", "closed"}
        )
        is_new = record.thread_id not in current.threads
        is_open_record = record.status not in {"completed", "failed", "closed"}
        if is_new and is_open_record and open_count >= int(self.policy.max_open_threads):
            raise ValueError("max_open_threads exceeded")
        current.threads[record.thread_id] = record.to_dict()
        return current

    def close_thread(self, state: SubagentState, thread_id: str, *, reason: str) -> SubagentState:
        current = state.copy()
        raw = current.threads.get(thread_id)
        if not isinstance(raw, dict):
            raise ValueError(f"unknown agent thread: {thread_id}")
        updated = copy.deepcopy(raw)
        updated["status"] = "closed"
        updated["close_reason"] = reason
        current.threads[thread_id] = updated
        return current

    def build_message(
        self,
        *,
        sender_agent_id: str,
        recipient_agent_id: str,
        thread_id: str,
        kind: str,
        content: str,
        iteration: int,
        correlation_id: str | None = None,
        reply_to_message_id: str | None = None,
        requires_ack: bool = False,
    ) -> AgentMessage:
        if len(content) > int(self.policy.max_message_chars):
            raise ValueError("message exceeds max_message_chars")
        return AgentMessage(
            message_id=f"msg-{uuid.uuid4()}",
            sender_agent_id=sender_agent_id,
            recipient_agent_id=recipient_agent_id,
            thread_id=thread_id,
            kind=kind or "followup",
            content=content,
            created_iteration=int(iteration),
            correlation_id=correlation_id,
            reply_to_message_id=reply_to_message_id,
            requires_ack=requires_ack,
        )

    def append_message(self, state: SubagentState, message: AgentMessage) -> SubagentState:
        current = state.copy()
        if not message.thread_id or message.thread_id not in current.threads:
            raise ValueError(f"unknown agent thread: {message.thread_id}")
        mailbox = current.mailboxes.setdefault(message.recipient_agent_id, [])
        if len(mailbox) >= int(self.policy.max_mailbox_messages):
            raise ValueError("max_mailbox_messages exceeded")
        mailbox.append(copy.deepcopy(message.to_dict()))
        return current

    def build_board_item(
        self,
        *,
        board_id: str,
        author_agent_id: str,
        kind: str,
        title: str,
        content: str,
        tags: tuple[str, ...],
        confidence: float | None,
        refs: tuple[str, ...],
        iteration: int,
        supersedes_item_id: str | None = None,
    ) -> BlackboardItem:
        if len(content) > int(self.policy.max_board_item_chars):
            raise ValueError("board item exceeds max_board_item_chars")
        return BlackboardItem(
            item_id=f"item-{uuid.uuid4()}",
            board_id=board_id or "default",
            author_agent_id=author_agent_id,
            kind=kind,
            title=title,
            content=content,
            tags=tuple(tags),
            confidence=confidence,
            refs=tuple(refs),
            created_iteration=int(iteration),
            supersedes_item_id=supersedes_item_id,
        )

    def append_board_item(self, state: SubagentState, item: BlackboardItem) -> SubagentState:
        current = state.copy()
        board = current.blackboards.setdefault(item.board_id, [])
        if len(board) >= int(self.policy.max_board_items):
            raise ValueError("max_board_items exceeded")
        board.append(copy.deepcopy(item.to_dict()))
        return current
