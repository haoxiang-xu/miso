# Agent Communication Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent agent threads, mailbox messaging, blackboard coordination, and return handoff on top of the existing subagent runtime without breaking current delegate/handoff/batch behavior.

**Architecture:** Extend `SubagentState` with serializable communication state, add focused communication dataclasses/helpers under `unchain.subagents`, then wire new reserved runtime tools through `SubagentToolPlugin`. Existing `delegate_to_subagent`, `handoff_to_subagent`, and `spawn_worker_batch` remain source-compatible and continue to pass their current tests while the new tools expose richer communication primitives.

**Tech Stack:** Python 3.12 dataclasses, existing unchain `ToolRuntimePlugin`/`ToolRuntimeOutcome`, existing fake `SequenceModelIO` style tests, `pytest`, GitNexus impact analysis.

---

## File Structure

- Modify `src/unchain/subagents/types.py`: add policy limits and new serializable state buckets.
- Create `src/unchain/subagents/communication.py`: typed records, validation helpers, mailbox/blackboard/thread state operations.
- Modify `src/unchain/subagents/runtime_tools.py`: add reserved tool builders for communication tools.
- Modify `src/unchain/subagents/plugin.py`: route new tool names, run foreground thread operations, emit communication events.
- Modify `src/unchain/agent/modules/subagents.py`: register new reserved tools.
- Modify `src/unchain/subagents/__init__.py`: export new builders and communication record types.
- Add `tests/test_subagent_communication_state.py`: state and helper tests.
- Add `tests/test_subagent_communication_tools.py`: end-to-end runtime tool tests.
- Keep `tests/test_kernel_subagents.py` passing unchanged.

## Implementation Notes

- Before editing each production symbol, run GitNexus impact analysis. Example: `npx gitnexus impact SubagentState --repo unchain --direction upstream`.
- If GitNexus reports HIGH or CRITICAL risk, stop and report the blast radius before continuing.
- Use `apply_patch` for manual edits.
- Run targeted tests after each task, then commit each task separately.
- The first implementation is foreground/in-process. `background=true` is accepted in payloads only if the tool result clearly says it is not running asynchronously yet.

### Task 1: Extend Subagent State and Policy

**Files:**
- Modify: `src/unchain/subagents/types.py`
- Test: `tests/test_subagent_communication_state.py`

- [ ] **Step 1: Run impact analysis**

Run:

```bash
npx gitnexus impact SubagentState --repo unchain --direction upstream
npx gitnexus impact SubagentPolicy --repo unchain --direction upstream
```

Expected: risk is not HIGH or CRITICAL. If it is HIGH or CRITICAL, report the direct callers and affected processes before editing.

- [ ] **Step 2: Write failing state tests**

Create `tests/test_subagent_communication_state.py` with:

```python
from unchain.subagents.types import SubagentPolicy, SubagentState


def test_subagent_state_preserves_communication_fields_across_copy_and_raw_roundtrip():
    state = SubagentState(root_agent_id="root", active_agent_id="root")
    state.threads["thread-1"] = {
        "thread_id": "thread-1",
        "agent_id": "root.worker.1",
        "parent_agent_id": "root",
        "target": "worker",
        "status": "idle",
        "lineage": ["root", "root.worker.1"],
    }
    state.mailboxes["root.worker.1"] = [
        {
            "message_id": "msg-1",
            "sender_agent_id": "root",
            "recipient_agent_id": "root.worker.1",
            "thread_id": "thread-1",
            "kind": "task",
            "content": "Check the bug",
        }
    ]
    state.blackboards["default"] = [
        {
            "item_id": "item-1",
            "board_id": "default",
            "author_agent_id": "root.worker.1",
            "kind": "finding",
            "title": "Bug source",
            "content": "The parser drops empty chunks.",
            "tags": ["parser"],
        }
    ]
    state.return_handoff_stack.append(
        {
            "frame_id": "frame-1",
            "parent_agent_id": "root",
            "child_agent_id": "root.specialist.1",
            "thread_id": "thread-2",
        }
    )

    copied = state.copy()
    copied.threads["thread-1"]["status"] = "closed"
    copied.mailboxes["root.worker.1"][0]["content"] = "changed"
    copied.blackboards["default"][0]["title"] = "changed"
    copied.return_handoff_stack[0]["frame_id"] = "changed"

    assert state.threads["thread-1"]["status"] == "idle"
    assert state.mailboxes["root.worker.1"][0]["content"] == "Check the bug"
    assert state.blackboards["default"][0]["title"] == "Bug source"
    assert state.return_handoff_stack[0]["frame_id"] == "frame-1"

    roundtripped = SubagentState.from_raw(state.to_dict())

    assert roundtripped.threads == state.threads
    assert roundtripped.mailboxes == state.mailboxes
    assert roundtripped.blackboards == state.blackboards
    assert roundtripped.return_handoff_stack == state.return_handoff_stack


def test_subagent_state_merges_communication_fields_without_dropping_existing_state():
    state = SubagentState(root_agent_id="root", active_agent_id="root")
    state.threads["existing"] = {"thread_id": "existing", "status": "idle"}
    state.mailboxes["root"] = [{"message_id": "old", "content": "old"}]

    merged = state.merged(
        {
            "threads": {"new": {"thread_id": "new", "status": "running"}},
            "mailboxes": {"child": [{"message_id": "new-msg", "content": "new"}]},
            "blackboards": {"default": [{"item_id": "item", "title": "Finding"}]},
            "return_handoff_stack": [{"frame_id": "frame"}],
        }
    )

    assert set(merged.threads) == {"existing", "new"}
    assert merged.mailboxes["root"][0]["message_id"] == "old"
    assert merged.mailboxes["child"][0]["message_id"] == "new-msg"
    assert merged.blackboards["default"][0]["item_id"] == "item"
    assert merged.return_handoff_stack[0]["frame_id"] == "frame"


def test_subagent_policy_has_conservative_communication_defaults():
    policy = SubagentPolicy()

    assert policy.max_open_threads == 10
    assert policy.max_mailbox_messages == 100
    assert policy.max_message_chars == 8000
    assert policy.max_board_items == 200
    assert policy.max_board_item_chars == 12000
    assert policy.allow_child_to_child_messages is False
    assert policy.allow_broadcast_messages is False
    assert policy.allow_return_handoff is True
    assert policy.retain_completed_threads is True
```

- [ ] **Step 3: Run the failing test**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_state.py -q
```

Expected: FAIL because `SubagentState` has no `threads`, `mailboxes`, `blackboards`, or `return_handoff_stack` fields, and `SubagentPolicy` has no communication policy fields.

- [ ] **Step 4: Extend `SubagentPolicy`**

In `src/unchain/subagents/types.py`, add these fields to `SubagentPolicy` after `handoff_requires_template`:

```python
    max_open_threads: int = 10
    max_mailbox_messages: int = 100
    max_message_chars: int = 8000
    max_board_items: int = 200
    max_board_item_chars: int = 12000
    allow_child_to_child_messages: bool = False
    allow_broadcast_messages: bool = False
    allow_return_handoff: bool = True
    retain_completed_threads: bool = True
```

- [ ] **Step 5: Extend `SubagentState` fields**

In `src/unchain/subagents/types.py`, add these dataclass fields after `running_batches`:

```python
    threads: dict[str, dict[str, Any]] = field(default_factory=dict)
    mailboxes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    blackboards: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    return_handoff_stack: list[dict[str, Any]] = field(default_factory=list)
```

- [ ] **Step 6: Update `SubagentState.copy()`**

Add these arguments to the `SubagentState(...)` constructor inside `copy()`:

```python
            threads=copy.deepcopy(self.threads),
            mailboxes=copy.deepcopy(self.mailboxes),
            blackboards=copy.deepcopy(self.blackboards),
            return_handoff_stack=copy.deepcopy(self.return_handoff_stack),
```

- [ ] **Step 7: Update `SubagentState.to_dict()`**

Add these keys to the returned dict:

```python
            "threads": copy.deepcopy(self.threads),
            "mailboxes": copy.deepcopy(self.mailboxes),
            "blackboards": copy.deepcopy(self.blackboards),
            "return_handoff_stack": copy.deepcopy(self.return_handoff_stack),
```

- [ ] **Step 8: Update `SubagentState.from_raw()`**

Add this block before `spawn_stats = raw.get("spawn_stats")`:

```python
        threads = raw.get("threads")
        if isinstance(threads, dict):
            state.threads = {
                str(key): copy.deepcopy(value)
                for key, value in threads.items()
                if isinstance(key, str) and isinstance(value, dict)
            }
        mailboxes = raw.get("mailboxes")
        if isinstance(mailboxes, dict):
            state.mailboxes = {
                str(key): [copy.deepcopy(item) for item in value if isinstance(item, dict)]
                for key, value in mailboxes.items()
                if isinstance(key, str) and isinstance(value, list)
            }
        blackboards = raw.get("blackboards")
        if isinstance(blackboards, dict):
            state.blackboards = {
                str(key): [copy.deepcopy(item) for item in value if isinstance(item, dict)]
                for key, value in blackboards.items()
                if isinstance(key, str) and isinstance(value, list)
            }
        return_handoff_stack = raw.get("return_handoff_stack")
        if isinstance(return_handoff_stack, list):
            state.return_handoff_stack = [
                copy.deepcopy(item)
                for item in return_handoff_stack
                if isinstance(item, dict)
            ]
```

- [ ] **Step 9: Update `SubagentState.merged()`**

Add this block before `if update.spawn_stats:`:

```python
        if update.threads:
            current.threads.update(copy.deepcopy(update.threads))
        if update.mailboxes:
            for key, value in update.mailboxes.items():
                current.mailboxes.setdefault(key, []).extend(copy.deepcopy(value))
        if update.blackboards:
            for key, value in update.blackboards.items():
                current.blackboards.setdefault(key, []).extend(copy.deepcopy(value))
        if update.return_handoff_stack:
            current.return_handoff_stack.extend(copy.deepcopy(update.return_handoff_stack))
```

- [ ] **Step 10: Run state tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_state.py -q
```

Expected: PASS.

- [ ] **Step 11: Run existing subagent tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_kernel_subagents.py tests/test_subagent_executor.py tests/test_subagent_warn_skip_passthrough.py -q
```

Expected: PASS.

- [ ] **Step 12: Commit**

Run:

```bash
git add src/unchain/subagents/types.py tests/test_subagent_communication_state.py
git commit -m "feat: add subagent communication state"
```

### Task 2: Add Communication Runtime Records and State Helpers

**Files:**
- Create: `src/unchain/subagents/communication.py`
- Modify: `src/unchain/subagents/__init__.py`
- Test: `tests/test_subagent_communication_state.py`

- [ ] **Step 1: Run impact analysis**

Run:

```bash
npx gitnexus impact SubagentState --repo unchain --direction upstream
```

Expected: risk is not HIGH or CRITICAL.

- [ ] **Step 2: Add failing helper tests**

Append to `tests/test_subagent_communication_state.py`:

```python
import pytest

from unchain.subagents.communication import (
    AgentCommunicationRuntime,
    AgentMessage,
    AgentThreadRecord,
    BlackboardItem,
)


def test_agent_thread_record_roundtrips_as_dict():
    record = AgentThreadRecord(
        thread_id="thread-1",
        agent_id="root.worker.1",
        parent_agent_id="root",
        target="worker",
        template_name="worker",
        mode="thread",
        status="idle",
        session_id="session:thread-1",
        memory_namespace="memory:thread-1",
        lineage=("root", "root.worker.1"),
        created_iteration=1,
        last_activity_iteration=2,
        context_mode="none",
        instructions="Stay focused.",
        expected_output="Summary",
    )

    raw = record.to_dict()
    parsed = AgentThreadRecord.from_raw(raw)

    assert parsed == record
    assert raw["lineage"] == ["root", "root.worker.1"]


def test_agent_message_rejects_oversized_content():
    runtime = AgentCommunicationRuntime(SubagentPolicy(max_message_chars=5))

    with pytest.raises(ValueError, match="message exceeds max_message_chars"):
        runtime.build_message(
            sender_agent_id="root",
            recipient_agent_id="root.worker.1",
            thread_id="thread-1",
            kind="followup",
            content="too long",
            iteration=1,
        )


def test_blackboard_item_rejects_oversized_content():
    runtime = AgentCommunicationRuntime(SubagentPolicy(max_board_item_chars=5))

    with pytest.raises(ValueError, match="board item exceeds max_board_item_chars"):
        runtime.build_board_item(
            board_id="default",
            author_agent_id="root.worker.1",
            kind="finding",
            title="Finding",
            content="too long",
            tags=("parser",),
            confidence=0.9,
            refs=("src/file.py:10",),
            iteration=1,
        )


def test_runtime_opens_thread_sends_message_and_closes_thread():
    runtime = AgentCommunicationRuntime(SubagentPolicy(max_open_threads=1))
    state = SubagentState(root_agent_id="root", active_agent_id="root")
    record = AgentThreadRecord(
        thread_id="thread-1",
        agent_id="root.worker.1",
        parent_agent_id="root",
        target="worker",
        template_name="worker",
        mode="thread",
        status="idle",
        session_id="session:thread-1",
        memory_namespace="memory:thread-1",
        lineage=("root", "root.worker.1"),
        created_iteration=1,
        last_activity_iteration=1,
        context_mode="none",
    )

    state = runtime.upsert_thread(state, record)
    message = runtime.build_message(
        sender_agent_id="root",
        recipient_agent_id="root.worker.1",
        thread_id="thread-1",
        kind="task",
        content="Inspect parser",
        iteration=1,
    )
    state = runtime.append_message(state, message)
    state = runtime.close_thread(state, "thread-1", reason="done")

    assert state.threads["thread-1"]["status"] == "closed"
    assert state.threads["thread-1"]["close_reason"] == "done"
    assert state.mailboxes["root.worker.1"][0]["content"] == "Inspect parser"
```

- [ ] **Step 3: Run the failing helper tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_state.py -q
```

Expected: FAIL because `unchain.subagents.communication` does not exist.

- [ ] **Step 4: Create `communication.py`**

Create `src/unchain/subagents/communication.py`:

```python
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
        if is_new and open_count >= int(self.policy.max_open_threads):
            raise ValueError("max_open_threads exceeded")
        current.threads[record.thread_id] = record.to_dict()
        return current

    def close_thread(self, state: SubagentState, thread_id: str, *, reason: str) -> SubagentState:
        current = state.copy()
        raw = current.threads.get(thread_id)
        if not isinstance(raw, dict):
            raise ValueError(f"unknown agent thread: {thread_id}")
        record = AgentThreadRecord.from_raw(raw)
        current.threads[thread_id] = AgentThreadRecord(
            thread_id=record.thread_id,
            agent_id=record.agent_id,
            parent_agent_id=record.parent_agent_id,
            target=record.target,
            template_name=record.template_name,
            mode=record.mode,
            status="closed",
            session_id=record.session_id,
            memory_namespace=record.memory_namespace,
            lineage=record.lineage,
            created_iteration=record.created_iteration,
            last_activity_iteration=record.last_activity_iteration,
            context_mode=record.context_mode,
            instructions=record.instructions,
            expected_output=record.expected_output,
            close_reason=reason,
        ).to_dict()
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
        if message.thread_id and message.thread_id not in current.threads:
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
```

- [ ] **Step 5: Export communication types**

In `src/unchain/subagents/__init__.py`, add these names to `__all__`:

```python
    "AgentCommunicationRuntime",
    "AgentMessage",
    "AgentThreadRecord",
    "BlackboardItem",
```

Add this set below `_EXECUTOR_EXPORTS`:

```python
_COMMUNICATION_EXPORTS = {
    "AgentCommunicationRuntime",
    "AgentMessage",
    "AgentThreadRecord",
    "BlackboardItem",
}
```

Add this branch to `__getattr__` before `_RUNTIME_TOOL_EXPORTS`:

```python
    if name in _COMMUNICATION_EXPORTS:
        module = importlib.import_module(".communication", __name__)
        return getattr(module, name)
```

- [ ] **Step 6: Run helper tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_state.py -q
```

Expected: PASS.

- [ ] **Step 7: Run import tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_unchain_imports.py tests/test_public_surface.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/unchain/subagents/communication.py src/unchain/subagents/__init__.py tests/test_subagent_communication_state.py
git commit -m "feat: add subagent communication runtime helpers"
```

### Task 3: Add Reserved Communication Tool Builders

**Files:**
- Modify: `src/unchain/subagents/runtime_tools.py`
- Modify: `src/unchain/subagents/__init__.py`
- Modify: `src/unchain/agent/modules/subagents.py`
- Test: `tests/test_subagent_communication_tools.py`

- [ ] **Step 1: Run impact analysis**

Run:

```bash
npx gitnexus impact SubagentModule.configure --repo unchain --direction upstream
npx gitnexus impact build_delegate_to_subagent_tool --repo unchain --direction upstream
```

Expected: risk is not HIGH or CRITICAL.

- [ ] **Step 2: Write failing tool builder tests**

Create `tests/test_subagent_communication_tools.py`:

```python
from unchain.subagents import (
    build_close_agent_thread_tool,
    build_read_agent_board_tool,
    build_return_handoff_to_subagent_tool,
    build_return_to_parent_tool,
    build_send_agent_message_tool,
    build_spawn_agent_thread_tool,
    build_wait_agent_messages_tool,
    build_write_agent_board_tool,
)


def test_communication_runtime_tool_builders_have_expected_names():
    tools = [
        build_spawn_agent_thread_tool(),
        build_send_agent_message_tool(),
        build_wait_agent_messages_tool(),
        build_close_agent_thread_tool(),
        build_write_agent_board_tool(),
        build_read_agent_board_tool(),
        build_return_handoff_to_subagent_tool(),
        build_return_to_parent_tool(),
    ]

    assert [tool.name for tool in tools] == [
        "spawn_agent_thread",
        "send_agent_message",
        "wait_agent_messages",
        "close_agent_thread",
        "write_agent_board",
        "read_agent_board",
        "return_handoff_to_subagent",
        "return_to_parent",
    ]
```

- [ ] **Step 3: Run the failing builder test**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_communication_runtime_tool_builders_have_expected_names -q
```

Expected: FAIL because the builders are not exported.

- [ ] **Step 4: Add builders to `runtime_tools.py`**

Append to `src/unchain/subagents/runtime_tools.py`:

```python
def build_spawn_agent_thread_tool() -> Tool:
    return tool(
        name="spawn_agent_thread",
        description="Spawn an addressable subagent thread that can receive follow-up messages.",
        func=lambda **_: {"error": "spawn_agent_thread is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="target", description="Template name or specialist role.", type_="string", required=True),
            ToolParameter(name="task", description="Initial task for the thread.", type_="string", required=True),
            ToolParameter(name="instructions", description="Extra execution instructions.", type_="string", required=False),
            ToolParameter(name="expected_output", description="Expected output contract.", type_="string", required=False),
            ToolParameter(name="context_mode", description="One of none, summary, last_n, or full.", type_="string", required=False),
            ToolParameter(name="background", description="Whether to keep the thread addressable after returning.", type_="boolean", required=False),
            ToolParameter(name="return_mode", description="One of result, return_to_parent, or terminal_handoff.", type_="string", required=False),
        ],
    )


def build_send_agent_message_tool() -> Tool:
    return tool(
        name="send_agent_message",
        description="Send a directed message to an open agent thread.",
        func=lambda **_: {"error": "send_agent_message is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="recipient", description="Recipient agent id or thread id.", type_="string", required=True),
            ToolParameter(name="content", description="Message content.", type_="string", required=True),
            ToolParameter(name="kind", description="Message kind such as followup, task, status, or result.", type_="string", required=False),
            ToolParameter(name="thread_id", description="Optional thread id when recipient is an agent id.", type_="string", required=False),
            ToolParameter(name="correlation_id", description="Optional caller correlation id.", type_="string", required=False),
            ToolParameter(name="requires_ack", description="Whether the sender expects acknowledgement.", type_="boolean", required=False),
        ],
    )


def build_wait_agent_messages_tool() -> Tool:
    return tool(
        name="wait_agent_messages",
        description="Inspect or wait for one or more agent threads to reach a condition.",
        func=lambda **_: {"error": "wait_agent_messages is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="thread_ids", description="Thread ids to inspect.", type_="array", required=True, items={"type": "string"}),
            ToolParameter(name="condition", description="One of any_done, all_done, or idle.", type_="string", required=False),
            ToolParameter(name="timeout_seconds", description="Maximum wait time in seconds.", type_="number", required=False),
        ],
    )


def build_close_agent_thread_tool() -> Tool:
    return tool(
        name="close_agent_thread",
        description="Close an open agent thread.",
        func=lambda **_: {"error": "close_agent_thread is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="thread_id", description="Thread id to close.", type_="string", required=True),
            ToolParameter(name="reason", description="Reason for closing the thread.", type_="string", required=False),
        ],
    )


def build_write_agent_board_tool() -> Tool:
    return tool(
        name="write_agent_board",
        description="Write a structured item to the shared agent blackboard.",
        func=lambda **_: {"error": "write_agent_board is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="kind", description="Item kind such as finding, evidence, plan, risk, decision, question, or summary.", type_="string", required=True),
            ToolParameter(name="title", description="Short item title.", type_="string", required=True),
            ToolParameter(name="content", description="Item content.", type_="string", required=True),
            ToolParameter(name="board_id", description="Board id. Defaults to default.", type_="string", required=False),
            ToolParameter(name="tags", description="Optional tags.", type_="array", required=False, items={"type": "string"}),
            ToolParameter(name="confidence", description="Optional confidence from 0 to 1.", type_="number", required=False),
            ToolParameter(name="refs", description="Optional source references.", type_="array", required=False, items={"type": "string"}),
            ToolParameter(name="supersedes_item_id", description="Optional item id superseded by this item.", type_="string", required=False),
        ],
    )


def build_read_agent_board_tool() -> Tool:
    return tool(
        name="read_agent_board",
        description="Read filtered items from the shared agent blackboard.",
        func=lambda **_: {"error": "read_agent_board is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="board_id", description="Board id. Defaults to default.", type_="string", required=False),
            ToolParameter(name="kinds", description="Optional item kinds to include.", type_="array", required=False, items={"type": "string"}),
            ToolParameter(name="tags", description="Optional tags to match.", type_="array", required=False, items={"type": "string"}),
            ToolParameter(name="author_agent_id", description="Optional author filter.", type_="string", required=False),
            ToolParameter(name="limit", description="Maximum number of items.", type_="integer", required=False),
        ],
    )


def build_return_handoff_to_subagent_tool() -> Tool:
    return tool(
        name="return_handoff_to_subagent",
        description="Temporarily hand control to a subagent and return the result to the parent.",
        func=lambda **_: {"error": "return_handoff_to_subagent is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="target", description="Registered subagent template name.", type_="string", required=True),
            ToolParameter(name="reason", description="Why temporary handoff is appropriate.", type_="string", required=False),
            ToolParameter(name="carry_context", description="Whether to pass current conversation context.", type_="boolean", required=False),
            ToolParameter(name="expected_return", description="Expected return contract.", type_="string", required=False),
        ],
    )


def build_return_to_parent_tool() -> Tool:
    return tool(
        name="return_to_parent",
        description="Return a temporary handoff result to the parent agent.",
        func=lambda **_: {"error": "return_to_parent is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="summary", description="Concise return summary.", type_="string", required=True),
            ToolParameter(name="result", description="Detailed result.", type_="string", required=False),
            ToolParameter(name="status", description="completed, blocked, or failed.", type_="string", required=False),
        ],
    )
```

- [ ] **Step 5: Register builders in `SubagentModule.configure()`**

In `src/unchain/agent/modules/subagents.py`, extend the import and add builder calls:

```python
    build_close_agent_thread_tool,
    build_read_agent_board_tool,
    build_return_handoff_to_subagent_tool,
    build_return_to_parent_tool,
    build_send_agent_message_tool,
    build_spawn_agent_thread_tool,
    build_wait_agent_messages_tool,
    build_write_agent_board_tool,
```

Add after the existing three `builder.add_tool(...)` calls:

```python
        builder.add_tool(build_spawn_agent_thread_tool())
        builder.add_tool(build_send_agent_message_tool())
        builder.add_tool(build_wait_agent_messages_tool())
        builder.add_tool(build_close_agent_thread_tool())
        builder.add_tool(build_write_agent_board_tool())
        builder.add_tool(build_read_agent_board_tool())
        builder.add_tool(build_return_handoff_to_subagent_tool())
        builder.add_tool(build_return_to_parent_tool())
```

- [ ] **Step 6: Export builders**

In `src/unchain/subagents/__init__.py`, add these names to `__all__` and `_RUNTIME_TOOL_EXPORTS`:

```python
    "build_close_agent_thread_tool",
    "build_read_agent_board_tool",
    "build_return_handoff_to_subagent_tool",
    "build_return_to_parent_tool",
    "build_send_agent_message_tool",
    "build_spawn_agent_thread_tool",
    "build_wait_agent_messages_tool",
    "build_write_agent_board_tool",
```

- [ ] **Step 7: Run builder tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_communication_runtime_tool_builders_have_expected_names -q
```

Expected: PASS.

- [ ] **Step 8: Run import tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_unchain_imports.py tests/test_public_surface.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

Run:

```bash
git add src/unchain/subagents/runtime_tools.py src/unchain/subagents/__init__.py src/unchain/agent/modules/subagents.py tests/test_subagent_communication_tools.py
git commit -m "feat: register subagent communication tools"
```

### Task 4: Implement Thread Spawn, Wait, and Close Tools

**Files:**
- Modify: `src/unchain/subagents/plugin.py`
- Modify: `src/unchain/subagents/communication.py`
- Test: `tests/test_subagent_communication_tools.py`

- [ ] **Step 1: Run impact analysis**

Run:

```bash
npx gitnexus impact SubagentToolPlugin --repo unchain --direction upstream
npx gitnexus impact SubagentToolPlugin.execute --repo unchain --direction upstream
npx gitnexus impact SubagentToolPlugin._run_child --repo unchain --direction upstream
```

Expected: risk is not HIGH or CRITICAL.

- [ ] **Step 2: Add failing thread lifecycle test**

Append to `tests/test_subagent_communication_tools.py`:

```python
import json

from unchain.agent import Agent, SubagentModule
from unchain.kernel import ModelTurnResult, ToolCall
from unchain.subagents import SubagentPolicy, SubagentTemplate


def _openai_tool_turn(*, call_id: str, name: str, arguments: dict) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[
            {
                "role": "assistant",
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            }
        ],
        tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        final_text="",
    )


def _text_turn(text: str) -> ModelTurnResult:
    return ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": text}],
        tool_calls=[],
        final_text=text,
    )


class SequenceModelIO:
    def __init__(self, provider: str, steps):
        self.provider = provider
        self.model = f"{provider}-model"
        self._steps = list(steps)
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        if not self._steps:
            raise AssertionError("unexpected model turn")
        step = self._steps.pop(0)
        if callable(step):
            return step(request)
        return step


def test_spawn_wait_and_close_agent_thread_records_state_and_result():
    child = Agent(
        name="researcher",
        provider="openai",
        model_io_factory=lambda spec, ctx: SequenceModelIO("openai", [_text_turn("thread result")]),
    )

    def _after_spawn(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_thread"
        assert payload["status"] == "completed"
        assert payload["summary"] == "thread result"
        assert payload["thread_id"]
        thread_id = payload["thread_id"]
        return _openai_tool_turn(
            call_id="call_2",
            name="wait_agent_messages",
            arguments={"thread_ids": [thread_id], "condition": "all_done"},
        )

    def _after_wait(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "completed"
        assert payload["threads"][0]["status"] == "completed"
        thread_id = payload["threads"][0]["thread_id"]
        return _openai_tool_turn(
            call_id="call_3",
            name="close_agent_thread",
            arguments={"thread_id": thread_id, "reason": "inspected"},
        )

    def _after_close(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "closed"
        assert payload["thread"]["close_reason"] == "inspected"
        return _text_turn("done")

    events = []
    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        agent=child,
                        allowed_modes=("delegate", "worker"),
                    ),
                ),
                policy=SubagentPolicy(max_open_threads=2),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="spawn_agent_thread",
                    arguments={"target": "researcher", "task": "Investigate"},
                ),
                _after_spawn,
                _after_wait,
                _after_close,
            ],
        ),
    )

    result = parent.run("start", max_iterations=4, run_id="root-run", callback=events.append)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"
    assert any(event["type"] == "agent_thread_spawned" for event in events)
    assert any(event["type"] == "agent_thread_completed" for event in events)
    assert any(event["type"] == "agent_thread_closed" for event in events)
```

- [ ] **Step 3: Run the failing lifecycle test**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_spawn_wait_and_close_agent_thread_records_state_and_result -q
```

Expected: FAIL because `SubagentToolPlugin` does not handle the new tools.

- [ ] **Step 4: Add communication tool name constants and imports**

In `src/unchain/subagents/plugin.py`, import:

```python
from .communication import AgentCommunicationRuntime, AgentThreadRecord
```

Add near the imports:

```python
_SUBAGENT_TOOL_NAMES = {"delegate_to_subagent", "handoff_to_subagent", "spawn_worker_batch"}
_COMMUNICATION_TOOL_NAMES = {
    "spawn_agent_thread",
    "send_agent_message",
    "wait_agent_messages",
    "close_agent_thread",
    "write_agent_board",
    "read_agent_board",
    "return_handoff_to_subagent",
    "return_to_parent",
}
```

Update `can_handle()`:

```python
        if tool_call.name not in (_SUBAGENT_TOOL_NAMES | _COMMUNICATION_TOOL_NAMES):
            return False
```

- [ ] **Step 5: Add runtime helper property**

Inside `SubagentToolPlugin`, add:

```python
    @property
    def communication_runtime(self) -> AgentCommunicationRuntime:
        return AgentCommunicationRuntime(self.policy)
```

- [ ] **Step 6: Route thread tools in `execute()`**

In `SubagentToolPlugin.execute()`, add branches after `spawn_worker_batch`:

```python
            if tool_call.name == "spawn_agent_thread":
                return self._spawn_agent_thread(tool_call=tool_call, context=context)
            if tool_call.name == "wait_agent_messages":
                return self._wait_agent_messages(tool_call=tool_call, context=context)
            if tool_call.name == "close_agent_thread":
                return self._close_agent_thread(tool_call=tool_call, context=context)
```

- [ ] **Step 7: Implement `_spawn_agent_thread()`**

Add this method before `_delegate()`:

```python
    def _spawn_agent_thread(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        target = str(args.get("target") or "").strip()
        task = str(args.get("task") or "").strip()
        instructions = str(args.get("instructions") or "").strip()
        expected_output = str(args.get("expected_output") or "").strip()
        context_mode = str(args.get("context_mode") or "none").strip() or "none"
        background = bool(args.get("background", False))
        if not target:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "spawn_agent_thread requires target"})
        if not task:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "spawn_agent_thread requires task"})
        state = self._ensure_state(context)
        parent_id = state.active_agent_id or self.parent_agent.name
        child_id, lineage, next_state = self._next_subagent_identity(state=state, target=target, mode="delegate")
        template = self._resolve_template(target, mode="delegate")
        child, memory_policy, template_name = self._build_subagent(
            template=template,
            child_id=child_id,
            lineage=lineage,
            mode="delegate",
            target=target,
            task=task,
            instructions=instructions,
            expected_output=expected_output,
        )
        thread_id = child_id
        session_id = f"{context.session_id or context.run_id}:{child_id}"
        memory_namespace = f"{context.memory_namespace or context.session_id or context.run_id}:{child_id}"
        child_run_id = self._build_child_run_id(session_id=context.session_id or context.run_id, child_id=child_id)
        record = AgentThreadRecord(
            thread_id=thread_id,
            agent_id=child_id,
            parent_agent_id=parent_id,
            target=target,
            template_name=template_name,
            mode="thread",
            status="running",
            session_id=session_id,
            memory_namespace=memory_namespace if memory_policy == "scoped_persistent" else "",
            lineage=tuple(lineage),
            created_iteration=int(context.iteration),
            last_activity_iteration=int(context.iteration),
            context_mode=context_mode,
            instructions=instructions,
            expected_output=expected_output,
        )
        runtime = self.communication_runtime
        try:
            threaded_state = runtime.upsert_thread(next_state, record)
        except Exception as exc:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": str(exc), "tool": tool_call.name})
        self._emit_subagent_event(
            context,
            "agent_thread_spawned",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="thread",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            thread_id=thread_id,
            background=background,
        )
        result = self._run_child(
            agent=child,
            mode="thread",
            child_id=child_id,
            lineage=lineage,
            template_name=template_name,
            session_id=session_id,
            memory_namespace=memory_namespace if memory_policy == "scoped_persistent" else "",
            input_messages=task,
            max_iterations=int(context.event.get("max_iterations") or 6),
            child_run_id=child_run_id,
            callback=context.callback,
            on_tool_confirm=context.event.get("on_tool_confirm"),
            on_human_input=context.event.get("on_human_input"),
            on_max_iterations=context.event.get("on_max_iterations"),
        )
        completed_record = AgentThreadRecord(
            thread_id=thread_id,
            agent_id=child_id,
            parent_agent_id=parent_id,
            target=target,
            template_name=template_name,
            mode="thread",
            status=result.status,
            session_id=session_id,
            memory_namespace=memory_namespace if memory_policy == "scoped_persistent" else "",
            lineage=tuple(lineage),
            created_iteration=int(context.iteration),
            last_activity_iteration=int(context.iteration),
            context_mode=context_mode,
            instructions=instructions,
            expected_output=expected_output,
        )
        threaded_state = runtime.upsert_thread(threaded_state, completed_record)
        self._emit_subagent_event(
            context,
            "agent_thread_completed",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="thread",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            thread_id=thread_id,
            status=result.status,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "agent_thread",
                "thread_id": thread_id,
                "agent_id": child_id,
                "template_name": template_name,
                "status": result.status,
                "summary": result.summary or result.output,
                "output": result.output,
                "lineage": list(lineage),
                "background": background,
            },
            state_updates={"subagent_state": threaded_state},
        )
```

- [ ] **Step 8: Implement `_wait_agent_messages()`**

Add:

```python
    def _wait_agent_messages(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        raw_thread_ids = args.get("thread_ids")
        if not isinstance(raw_thread_ids, list) or not raw_thread_ids:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "wait_agent_messages requires thread_ids"})
        thread_ids = [str(item) for item in raw_thread_ids if isinstance(item, str) and item]
        state = self._ensure_state(context)
        threads = []
        for thread_id in thread_ids:
            raw = state.threads.get(thread_id)
            if isinstance(raw, dict):
                threads.append(copy.deepcopy(raw))
            else:
                threads.append({"thread_id": thread_id, "status": "not_found"})
        completed_statuses = {"completed", "failed", "closed"}
        all_done = all(item.get("status") in completed_statuses for item in threads)
        status = "completed" if all_done else "running"
        self._emit_subagent_event(
            context,
            "agent_message_wait_completed",
            subagent_id=state.active_agent_id or self.parent_agent.name,
            parent_id=state.active_agent_id or self.parent_agent.name,
            mode="thread",
            template=None,
            lineage=list(state.active_lineage or [self.parent_agent.name]),
            status=status,
            thread_ids=thread_ids,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={"mode": "agent_wait", "status": status, "threads": threads},
        )
```

- [ ] **Step 9: Implement `_close_agent_thread()`**

Add:

```python
    def _close_agent_thread(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        thread_id = str(args.get("thread_id") or "").strip()
        reason = str(args.get("reason") or "").strip()
        if not thread_id:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "close_agent_thread requires thread_id"})
        state = self._ensure_state(context)
        try:
            next_state = self.communication_runtime.close_thread(state, thread_id, reason=reason)
        except Exception as exc:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": str(exc), "tool": tool_call.name})
        thread = copy.deepcopy(next_state.threads[thread_id])
        self._emit_subagent_event(
            context,
            "agent_thread_closed",
            subagent_id=str(thread.get("agent_id") or thread_id),
            parent_id=str(thread.get("parent_agent_id") or self.parent_agent.name),
            mode="thread",
            template=str(thread.get("template_name") or "") or None,
            lineage=thread.get("lineage") if isinstance(thread.get("lineage"), list) else [],
            thread_id=thread_id,
            reason=reason,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={"mode": "agent_thread_close", "status": "closed", "thread": thread},
            state_updates={"subagent_state": next_state},
        )
```

- [ ] **Step 10: Run lifecycle test**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_spawn_wait_and_close_agent_thread_records_state_and_result -q
```

Expected: PASS.

- [ ] **Step 11: Run existing subagent tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_kernel_subagents.py tests/test_subagent_executor.py tests/test_subagent_warn_skip_passthrough.py -q
```

Expected: PASS.

- [ ] **Step 12: Commit**

Run:

```bash
git add src/unchain/subagents/plugin.py src/unchain/subagents/communication.py tests/test_subagent_communication_tools.py
git commit -m "feat: add subagent thread lifecycle tools"
```

### Task 5: Implement Mailbox Follow-Up

**Files:**
- Modify: `src/unchain/subagents/plugin.py`
- Modify: `src/unchain/subagents/communication.py`
- Test: `tests/test_subagent_communication_tools.py`

- [ ] **Step 1: Run impact analysis**

Run:

```bash
npx gitnexus impact AgentCommunicationRuntime --repo unchain --direction upstream
npx gitnexus impact SubagentToolPlugin.execute --repo unchain --direction upstream
```

Expected: risk is not HIGH or CRITICAL.

- [ ] **Step 2: Add failing mailbox follow-up test**

Append to `tests/test_subagent_communication_tools.py`:

```python
def test_send_agent_message_runs_followup_on_existing_thread_session():
    child_requests = []

    def child_factory(spec, ctx):
        def _fetch(request):
            child_requests.append(
                {
                    "session_id": ctx.session_id,
                    "memory_namespace": ctx.memory_namespace,
                    "last_content": request.messages[-1]["content"],
                }
            )
            if request.messages[-1]["content"] == "Initial task":
                return _text_turn("initial done")
            if request.messages[-1]["content"] == "Follow up":
                return _text_turn("followup done")
            raise AssertionError(f"unexpected child content: {request.messages[-1]['content']}")

        return SequenceModelIO("openai", [_fetch])

    child = Agent(name="researcher", provider="openai", model_io_factory=child_factory)

    def _after_spawn(request):
        payload = json.loads(request.messages[-1]["output"])
        return _openai_tool_turn(
            call_id="call_2",
            name="send_agent_message",
            arguments={
                "recipient": payload["thread_id"],
                "content": "Follow up",
                "kind": "followup",
            },
        )

    def _after_send(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "agent_message"
        assert payload["status"] == "completed"
        assert payload["reply"]["summary"] == "followup done"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        agent=child,
                        allowed_modes=("delegate",),
                        memory_policy="scoped_persistent",
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="spawn_agent_thread",
                    arguments={"target": "researcher", "task": "Initial task"},
                ),
                _after_spawn,
                _after_send,
            ],
        ),
    )

    result = parent.run("start", max_iterations=3, session_id="root-session", memory_namespace="root-ns")

    assert result.status == "completed"
    assert [item["last_content"] for item in child_requests] == ["Initial task", "Follow up"]
    assert child_requests[0]["session_id"] == child_requests[1]["session_id"]
    assert child_requests[0]["memory_namespace"] == child_requests[1]["memory_namespace"]
```

- [ ] **Step 3: Add failing child-to-sibling policy test**

Append:

```python
def test_send_agent_message_rejects_unknown_or_closed_thread():
    parent = Agent(
        name="manager",
        provider="openai",
        modules=(SubagentModule(),),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="send_agent_message",
                    arguments={"recipient": "missing-thread", "content": "hello"},
                ),
                lambda request: _text_turn(json.loads(request.messages[-1]["output"])["error"]),
            ],
        ),
    )

    result = parent.run("start", max_iterations=2)

    assert result.status == "completed"
    assert "unknown agent thread" in result.messages[-1]["content"]
```

- [ ] **Step 4: Run failing mailbox tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_send_agent_message_runs_followup_on_existing_thread_session tests/test_subagent_communication_tools.py::test_send_agent_message_rejects_unknown_or_closed_thread -q
```

Expected: FAIL because `send_agent_message` is not routed.

- [ ] **Step 5: Add runtime thread lookup helper**

In `src/unchain/subagents/communication.py`, add to `AgentCommunicationRuntime`:

```python
    def require_thread(self, state: SubagentState, recipient: str, explicit_thread_id: str = "") -> AgentThreadRecord:
        thread_id = explicit_thread_id or recipient
        raw = state.threads.get(thread_id)
        if raw is None:
            for candidate in state.threads.values():
                if isinstance(candidate, dict) and candidate.get("agent_id") == recipient:
                    raw = candidate
                    break
        if not isinstance(raw, dict):
            raise ValueError(f"unknown agent thread: {thread_id}")
        record = AgentThreadRecord.from_raw(raw)
        if record.status == "closed":
            raise ValueError(f"agent thread is closed: {record.thread_id}")
        return record
```

- [ ] **Step 6: Route `send_agent_message`**

In `SubagentToolPlugin.execute()`, add:

```python
            if tool_call.name == "send_agent_message":
                return self._send_agent_message(tool_call=tool_call, context=context)
```

- [ ] **Step 7: Implement `_send_agent_message()`**

Add this method near the other communication handlers:

```python
    def _send_agent_message(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        recipient = str(args.get("recipient") or "").strip()
        content = str(args.get("content") or "").strip()
        kind = str(args.get("kind") or "followup").strip() or "followup"
        explicit_thread_id = str(args.get("thread_id") or "").strip()
        if not recipient:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "send_agent_message requires recipient"})
        if not content:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "send_agent_message requires content"})
        state = self._ensure_state(context)
        runtime = self.communication_runtime
        try:
            record = runtime.require_thread(state, recipient, explicit_thread_id)
            message = runtime.build_message(
                sender_agent_id=state.active_agent_id or self.parent_agent.name,
                recipient_agent_id=record.agent_id,
                thread_id=record.thread_id,
                kind=kind,
                content=content,
                iteration=int(context.iteration),
                correlation_id=str(args.get("correlation_id") or "") or None,
                requires_ack=bool(args.get("requires_ack", False)),
            )
            next_state = runtime.append_message(state, message)
        except Exception as exc:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": str(exc), "tool": tool_call.name})
        template = self._resolve_template(record.target, mode="delegate")
        child, memory_policy, template_name = self._build_subagent(
            template=template,
            child_id=record.agent_id,
            lineage=list(record.lineage),
            mode="delegate",
            target=record.target,
            task=content,
            instructions=record.instructions,
            expected_output=record.expected_output,
        )
        child_run_id = self._build_child_run_id(session_id=context.session_id or context.run_id, child_id=record.agent_id)
        self._emit_subagent_event(
            context,
            "agent_message_sent",
            subagent_id=record.agent_id,
            parent_id=record.parent_agent_id,
            mode="message",
            template=template_name,
            lineage=list(record.lineage),
            thread_id=record.thread_id,
            child_run_id=child_run_id,
            message_id=message.message_id,
            kind=kind,
        )
        result = self._run_child(
            agent=child,
            mode="message",
            child_id=record.agent_id,
            lineage=list(record.lineage),
            template_name=template_name,
            session_id=record.session_id,
            memory_namespace=record.memory_namespace if memory_policy == "scoped_persistent" else "",
            input_messages=content,
            max_iterations=int(context.event.get("max_iterations") or 6),
            child_run_id=child_run_id,
            callback=context.callback,
            on_tool_confirm=context.event.get("on_tool_confirm"),
            on_human_input=context.event.get("on_human_input"),
            on_max_iterations=context.event.get("on_max_iterations"),
        )
        completed_record = AgentThreadRecord(
            thread_id=record.thread_id,
            agent_id=record.agent_id,
            parent_agent_id=record.parent_agent_id,
            target=record.target,
            template_name=record.template_name,
            mode=record.mode,
            status=result.status,
            session_id=record.session_id,
            memory_namespace=record.memory_namespace,
            lineage=record.lineage,
            created_iteration=record.created_iteration,
            last_activity_iteration=int(context.iteration),
            context_mode=record.context_mode,
            instructions=record.instructions,
            expected_output=record.expected_output,
            close_reason=record.close_reason,
        )
        next_state = runtime.upsert_thread(next_state, completed_record)
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "agent_message",
                "status": result.status,
                "thread_id": record.thread_id,
                "message": message.to_dict(),
                "reply": result.to_dict(),
            },
            state_updates={"subagent_state": next_state},
        )
```

- [ ] **Step 8: Run mailbox tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_send_agent_message_runs_followup_on_existing_thread_session tests/test_subagent_communication_tools.py::test_send_agent_message_rejects_unknown_or_closed_thread -q
```

Expected: PASS.

- [ ] **Step 9: Run communication tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_state.py tests/test_subagent_communication_tools.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add src/unchain/subagents/communication.py src/unchain/subagents/plugin.py tests/test_subagent_communication_tools.py
git commit -m "feat: add subagent mailbox followups"
```

### Task 6: Implement Blackboard Tools

**Files:**
- Modify: `src/unchain/subagents/plugin.py`
- Modify: `src/unchain/subagents/communication.py`
- Test: `tests/test_subagent_communication_tools.py`

- [ ] **Step 1: Run impact analysis**

Run:

```bash
npx gitnexus impact AgentCommunicationRuntime --repo unchain --direction upstream
npx gitnexus impact SubagentToolPlugin.execute --repo unchain --direction upstream
```

Expected: risk is not HIGH or CRITICAL.

- [ ] **Step 2: Add failing blackboard test**

Append to `tests/test_subagent_communication_tools.py`:

```python
def test_write_and_read_agent_board_filters_items():
    def _after_write_first(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "written"
        return _openai_tool_turn(
            call_id="call_2",
            name="write_agent_board",
            arguments={
                "kind": "risk",
                "title": "Missing tests",
                "content": "No regression coverage.",
                "tags": ["tests"],
                "refs": ["tests/test_example.py:1"],
            },
        )

    def _after_write_second(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["item"]["kind"] == "risk"
        return _openai_tool_turn(
            call_id="call_3",
            name="read_agent_board",
            arguments={"kinds": ["finding"], "tags": ["parser"]},
        )

    def _after_read(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "ok"
        assert len(payload["items"]) == 1
        assert payload["items"][0]["title"] == "Parser bug"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(SubagentModule(),),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="write_agent_board",
                    arguments={
                        "kind": "finding",
                        "title": "Parser bug",
                        "content": "The parser drops empty chunks.",
                        "tags": ["parser"],
                        "confidence": 0.8,
                        "refs": ["src/parser.py:10"],
                    },
                ),
                _after_write_first,
                _after_write_second,
                _after_read,
            ],
        ),
    )

    result = parent.run("start", max_iterations=4)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "done"
```

- [ ] **Step 3: Run the failing blackboard test**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_write_and_read_agent_board_filters_items -q
```

Expected: FAIL because `write_agent_board` and `read_agent_board` are not routed.

- [ ] **Step 4: Add board read helper**

In `src/unchain/subagents/communication.py`, add:

```python
    def read_board_items(
        self,
        state: SubagentState,
        *,
        board_id: str,
        kinds: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
        author_agent_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        board = state.blackboards.get(board_id or "default", [])
        items: list[dict[str, Any]] = []
        tag_set = set(tags)
        kind_set = set(kinds)
        for raw in board:
            if not isinstance(raw, dict):
                continue
            if kind_set and raw.get("kind") not in kind_set:
                continue
            if author_agent_id and raw.get("author_agent_id") != author_agent_id:
                continue
            raw_tags = raw.get("tags")
            raw_tag_set = set(raw_tags if isinstance(raw_tags, list) else [])
            if tag_set and not tag_set.issubset(raw_tag_set):
                continue
            items.append(copy.deepcopy(raw))
        return items[-max(1, int(limit)):]
```

- [ ] **Step 5: Route board tools**

In `SubagentToolPlugin.execute()`, add:

```python
            if tool_call.name == "write_agent_board":
                return self._write_agent_board(tool_call=tool_call, context=context)
            if tool_call.name == "read_agent_board":
                return self._read_agent_board(tool_call=tool_call, context=context)
```

- [ ] **Step 6: Implement `_write_agent_board()`**

Add:

```python
    def _write_agent_board(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        kind = str(args.get("kind") or "").strip()
        title = str(args.get("title") or "").strip()
        content = str(args.get("content") or "").strip()
        if not kind:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "write_agent_board requires kind"})
        if not title:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "write_agent_board requires title"})
        if not content:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "write_agent_board requires content"})
        state = self._ensure_state(context)
        tags = tuple(str(item) for item in args.get("tags", []) if isinstance(item, str)) if isinstance(args.get("tags"), list) else ()
        refs = tuple(str(item) for item in args.get("refs", []) if isinstance(item, str)) if isinstance(args.get("refs"), list) else ()
        confidence = args.get("confidence")
        confidence_value = float(confidence) if isinstance(confidence, (int, float)) else None
        try:
            item = self.communication_runtime.build_board_item(
                board_id=str(args.get("board_id") or "default"),
                author_agent_id=state.active_agent_id or self.parent_agent.name,
                kind=kind,
                title=title,
                content=content,
                tags=tags,
                confidence=confidence_value,
                refs=refs,
                iteration=int(context.iteration),
                supersedes_item_id=str(args.get("supersedes_item_id") or "") or None,
            )
            next_state = self.communication_runtime.append_board_item(state, item)
        except Exception as exc:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": str(exc), "tool": tool_call.name})
        self._emit_subagent_event(
            context,
            "agent_board_item_written",
            subagent_id=state.active_agent_id or self.parent_agent.name,
            parent_id=state.active_agent_id or self.parent_agent.name,
            mode="board",
            template=None,
            lineage=list(state.active_lineage or [self.parent_agent.name]),
            board_id=item.board_id,
            item_id=item.item_id,
            kind=item.kind,
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={"mode": "agent_board_write", "status": "written", "item": item.to_dict()},
            state_updates={"subagent_state": next_state},
        )
```

- [ ] **Step 7: Implement `_read_agent_board()`**

Add:

```python
    def _read_agent_board(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        state = self._ensure_state(context)
        kinds = tuple(str(item) for item in args.get("kinds", []) if isinstance(item, str)) if isinstance(args.get("kinds"), list) else ()
        tags = tuple(str(item) for item in args.get("tags", []) if isinstance(item, str)) if isinstance(args.get("tags"), list) else ()
        raw_limit = args.get("limit")
        limit = int(raw_limit) if isinstance(raw_limit, int) else 50
        items = self.communication_runtime.read_board_items(
            state,
            board_id=str(args.get("board_id") or "default"),
            kinds=kinds,
            tags=tags,
            author_agent_id=str(args.get("author_agent_id") or ""),
            limit=limit,
        )
        self._emit_subagent_event(
            context,
            "agent_board_read",
            subagent_id=state.active_agent_id or self.parent_agent.name,
            parent_id=state.active_agent_id or self.parent_agent.name,
            mode="board",
            template=None,
            lineage=list(state.active_lineage or [self.parent_agent.name]),
            board_id=str(args.get("board_id") or "default"),
            count=len(items),
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={"mode": "agent_board_read", "status": "ok", "items": items, "count": len(items)},
        )
```

- [ ] **Step 8: Run blackboard test**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_write_and_read_agent_board_filters_items -q
```

Expected: PASS.

- [ ] **Step 9: Run communication tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_state.py tests/test_subagent_communication_tools.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add src/unchain/subagents/communication.py src/unchain/subagents/plugin.py tests/test_subagent_communication_tools.py
git commit -m "feat: add subagent blackboard tools"
```

### Task 7: Implement Return Handoff

**Files:**
- Modify: `src/unchain/subagents/plugin.py`
- Modify: `src/unchain/subagents/communication.py`
- Test: `tests/test_subagent_communication_tools.py`

- [ ] **Step 1: Run impact analysis**

Run:

```bash
npx gitnexus impact SubagentToolPlugin._handoff --repo unchain --direction upstream
npx gitnexus impact SubagentToolPlugin._run_child --repo unchain --direction upstream
```

Expected: risk is not HIGH or CRITICAL.

- [ ] **Step 2: Add failing return handoff test**

Append to `tests/test_subagent_communication_tools.py`:

```python
def test_return_handoff_returns_control_to_parent():
    child = Agent(
        name="specialist",
        provider="openai",
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="child_return",
                    name="return_to_parent",
                    arguments={
                        "summary": "specialist summary",
                        "result": "specialist detailed result",
                        "status": "completed",
                    },
                ),
                _text_turn("specialist final text"),
            ],
        ),
    )

    def _after_return_handoff(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["mode"] == "return_handoff"
        assert payload["status"] == "completed"
        assert payload["return"]["summary"] == "specialist summary"
        assert payload["return"]["result"] == "specialist detailed result"
        return _text_turn("parent resumed")

    events = []
    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="specialist",
                        description="Temporary specialist",
                        agent=child,
                        allowed_modes=("handoff", "delegate"),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="return_handoff_to_subagent",
                    arguments={
                        "target": "specialist",
                        "reason": "Needs temporary expertise",
                        "carry_context": True,
                    },
                ),
                _after_return_handoff,
            ],
        ),
    )

    result = parent.run("Need temporary help", max_iterations=2, callback=events.append)

    assert result.status == "completed"
    assert result.messages[-1]["content"] == "parent resumed"
    assert any(event["type"] == "subagent_return_handoff_started" for event in events)
    assert any(event["type"] == "subagent_return_handoff_completed" for event in events)
```

- [ ] **Step 3: Run the failing return handoff test**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_return_handoff_returns_control_to_parent -q
```

Expected: FAIL because return handoff tools are not routed.

- [ ] **Step 4: Route return tools**

In `SubagentToolPlugin.execute()`, add:

```python
            if tool_call.name == "return_handoff_to_subagent":
                return self._return_handoff_to_subagent(tool_call=tool_call, context=context)
            if tool_call.name == "return_to_parent":
                return self._return_to_parent(tool_call=tool_call, context=context)
```

- [ ] **Step 5: Implement return payload extractor**

Add this module-level helper in `src/unchain/subagents/plugin.py` near `_last_assistant_text`:

```python
def _extract_return_to_parent_payload(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        output = message.get("output")
        if isinstance(output, str) and output.strip():
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("mode") == "return_to_parent":
                return copy.deepcopy(parsed)
        content = message.get("content")
        if isinstance(content, list):
            for block in reversed(content):
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and parsed.get("mode") == "return_to_parent":
                    return copy.deepcopy(parsed)
    return None
```

- [ ] **Step 6: Implement `_return_to_parent()`**

Add:

```python
    def _return_to_parent(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        args = _parse_arguments(tool_call.arguments)
        summary = str(args.get("summary") or "").strip()
        result = str(args.get("result") or "").strip()
        status = str(args.get("status") or "completed").strip() or "completed"
        if not summary:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "return_to_parent requires summary"})
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "return_to_parent",
                "status": status,
                "summary": summary,
                "result": result,
            },
        )
```

- [ ] **Step 7: Implement `_return_handoff_to_subagent()`**

Add:

```python
    def _return_handoff_to_subagent(self, *, tool_call: ToolCall, context) -> ToolRuntimeOutcome:
        if not self.policy.allow_return_handoff:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "return handoff is disabled by policy"})
        args = _parse_arguments(tool_call.arguments)
        target = str(args.get("target") or "").strip()
        reason = str(args.get("reason") or "").strip()
        expected_return = str(args.get("expected_return") or "").strip()
        carry_context = bool(args.get("carry_context", True))
        if not target:
            return ToolRuntimeOutcome(handled=True, tool_result={"error": "return_handoff_to_subagent requires target"})
        state = self._ensure_state(context)
        parent_id = state.active_agent_id or self.parent_agent.name
        child_id, lineage, next_state = self._next_subagent_identity(state=state, target=target, mode="handoff")
        template = self._resolve_template(target, mode="handoff")
        child, memory_policy, template_name = self._build_subagent(
            template=template,
            child_id=child_id,
            lineage=lineage,
            mode="handoff",
            target=target,
            task=reason or "Temporarily take over this segment and return to the parent.",
            instructions="Return control to the parent when your segment is complete.",
            expected_output=expected_return or "Return a concise summary and result to the parent.",
        )
        session_id = f"{context.session_id or context.run_id}:{child_id}"
        memory_namespace = f"{context.memory_namespace or context.session_id or context.run_id}:{child_id}"
        child_run_id = self._build_child_run_id(session_id=context.session_id or context.run_id, child_id=child_id)
        frame = {
            "frame_id": child_run_id,
            "parent_agent_id": parent_id,
            "child_agent_id": child_id,
            "thread_id": child_id,
            "target": target,
        }
        next_state = next_state.merged({"return_handoff_stack": [frame]})
        self._emit_subagent_event(
            context,
            "subagent_return_handoff_started",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="return_handoff",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
        )
        sanitized_messages = _sanitize_handoff_messages(context.latest_messages(), tool_call=tool_call)
        input_messages: str | list[dict[str, Any]] = sanitized_messages if carry_context else reason or "Continue this segment."
        child_result = self._run_child(
            agent=child,
            mode="return_handoff",
            child_id=child_id,
            lineage=lineage,
            template_name=template_name,
            session_id=session_id,
            memory_namespace=memory_namespace if memory_policy == "scoped_persistent" else "",
            input_messages=input_messages,
            max_iterations=int(context.event.get("max_iterations") or 6),
            child_run_id=child_run_id,
            callback=context.callback,
            on_tool_confirm=context.event.get("on_tool_confirm"),
            on_human_input=context.event.get("on_human_input"),
            on_max_iterations=context.event.get("on_max_iterations"),
        )
        return_payload = _extract_return_to_parent_payload(child_result.messages) or {
            "mode": "return_to_parent",
            "status": child_result.status,
            "summary": child_result.summary or child_result.output,
            "result": child_result.output,
        }
        completed_state = next_state.copy()
        completed_state.return_handoff_stack = [
            item
            for item in completed_state.return_handoff_stack
            if not (isinstance(item, dict) and item.get("frame_id") == child_run_id)
        ]
        self._emit_subagent_event(
            context,
            "subagent_return_handoff_completed",
            subagent_id=child_id,
            parent_id=parent_id,
            mode="return_handoff",
            template=template_name,
            lineage=lineage,
            child_run_id=child_run_id,
            status=str(return_payload.get("status") or child_result.status),
        )
        return ToolRuntimeOutcome(
            handled=True,
            tool_result={
                "mode": "return_handoff",
                "status": str(return_payload.get("status") or child_result.status),
                "agent_name": child.name,
                "template_name": template_name,
                "lineage": list(lineage),
                "return": return_payload,
                "summary": str(return_payload.get("summary") or child_result.summary or child_result.output),
            },
            state_updates={"subagent_state": completed_state},
        )
```

- [ ] **Step 8: Run return handoff test**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_return_handoff_returns_control_to_parent -q
```

Expected: PASS.

- [ ] **Step 9: Run communication tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_state.py tests/test_subagent_communication_tools.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add src/unchain/subagents/plugin.py tests/test_subagent_communication_tools.py
git commit -m "feat: add subagent return handoff"
```

### Task 8: Compatibility, Event Coverage, and Final Verification

**Files:**
- Modify if needed: `src/unchain/subagents/plugin.py`
- Modify if needed: `src/unchain/events/normalizer.py`
- Test: `tests/test_kernel_subagents.py`
- Test: `tests/test_subagent_communication_tools.py`

- [ ] **Step 1: Run impact analysis if production code changes are needed**

Run this before touching `plugin.py`:

```bash
npx gitnexus impact SubagentToolPlugin --repo unchain --direction upstream
```

Run this before touching `normalizer.py`:

```bash
npx gitnexus impact normalize_raw_event --repo unchain --direction upstream
```

Expected: risk is not HIGH or CRITICAL.

- [ ] **Step 2: Add event assertion test**

Append to `tests/test_subagent_communication_tools.py`:

```python
def test_communication_events_include_thread_and_board_identifiers():
    events = []
    child = Agent(
        name="researcher",
        provider="openai",
        model_io_factory=lambda spec, ctx: SequenceModelIO("openai", [_text_turn("thread done")]),
    )

    def _after_spawn(request):
        payload = json.loads(request.messages[-1]["output"])
        return _openai_tool_turn(
            call_id="call_2",
            name="write_agent_board",
            arguments={
                "kind": "summary",
                "title": "Thread result",
                "content": payload["summary"],
                "tags": ["thread"],
            },
        )

    def _after_board(request):
        payload = json.loads(request.messages[-1]["output"])
        assert payload["status"] == "written"
        return _text_turn("done")

    parent = Agent(
        name="manager",
        provider="openai",
        modules=(
            SubagentModule(
                templates=(
                    SubagentTemplate(
                        name="researcher",
                        description="Research specialist",
                        agent=child,
                        allowed_modes=("delegate",),
                    ),
                ),
            ),
        ),
        model_io_factory=lambda spec, ctx: SequenceModelIO(
            "openai",
            [
                _openai_tool_turn(
                    call_id="call_1",
                    name="spawn_agent_thread",
                    arguments={"target": "researcher", "task": "Investigate"},
                ),
                _after_spawn,
                _after_board,
            ],
        ),
    )

    result = parent.run("start", max_iterations=3, run_id="root-run", callback=events.append)

    assert result.status == "completed"
    spawned = next(event for event in events if event["type"] == "agent_thread_spawned")
    board = next(event for event in events if event["type"] == "agent_board_item_written")
    assert spawned["root_run_id"] == "root-run"
    assert spawned["thread_id"]
    assert board["board_id"] == "default"
    assert board["item_id"].startswith("item-")
```

- [ ] **Step 3: Run event test**

Run:

```bash
PYTHONPATH=src pytest tests/test_subagent_communication_tools.py::test_communication_events_include_thread_and_board_identifiers -q
```

Expected: PASS. If it fails due missing event extras, add those extras to the matching `_emit_subagent_event(...)` calls in `plugin.py`.

- [ ] **Step 4: Run all subagent tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_kernel_subagents.py tests/test_subagent_executor.py tests/test_subagent_warn_skip_passthrough.py tests/test_subagent_communication_state.py tests/test_subagent_communication_tools.py -q
```

Expected: PASS.

- [ ] **Step 5: Run import and public surface tests**

Run:

```bash
PYTHONPATH=src pytest tests/test_unchain_imports.py tests/test_public_surface.py -q
```

Expected: PASS.

- [ ] **Step 6: Run broader test suite**

Run:

```bash
PYTHONPATH=src pytest tests/ -q
```

Expected: PASS, except for known flaky tests called out in `AGENTS.md` if they fail independently:

- `test_read_file_ast_parses_python_file`
- `test_pinned_prompt_messages_relocate_non_python_ranges_via_declaration_metadata`

- [ ] **Step 7: Run GitNexus detect changes**

Run:

```bash
npx gitnexus detect-changes --repo unchain
```

Expected: affected symbols are limited to subagent communication files, subagent module registration, runtime tool builders, and tests. Risk should not be HIGH or CRITICAL.

- [ ] **Step 8: Commit final compatibility adjustments**

If Step 3 required code changes, run:

```bash
git add src/unchain/subagents/plugin.py src/unchain/events/normalizer.py tests/test_subagent_communication_tools.py
git commit -m "test: verify subagent communication events"
```

If Step 3 required no code changes, run:

```bash
git add tests/test_subagent_communication_tools.py
git commit -m "test: cover subagent communication events"
```

## Self-Review

Spec coverage:

- Persistent threads: Tasks 1, 2, 3, 4, and 8.
- Mailbox: Tasks 1, 2, 3, 5, and 8.
- Blackboard: Tasks 1, 2, 3, 6, and 8.
- Return handoff: Tasks 3 and 7.
- Existing behavior compatibility: Tasks 4, 5, 6, 7, and 8 run the existing subagent suites.
- Policy limits: Tasks 1, 2, and 5 cover message/thread limits; Task 6 covers board limits through helper behavior.
- Event visibility: Tasks 4, 6, 7, and 8 cover raw callback events.

Marker scan:

- No task uses unresolved markers.
- Every production edit step names exact files and code blocks.
- Every test step includes exact commands and expected outcomes.

Type consistency:

- `AgentThreadRecord`, `AgentMessage`, and `BlackboardItem` are defined in Task 2 and reused consistently in later tasks.
- New state field names are `threads`, `mailboxes`, `blackboards`, and `return_handoff_stack` throughout.
- Tool names match the reserved builder names from Task 3.
