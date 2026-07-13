# Agent Communication Layer Design

Date: 2026-07-06
Status: Draft for review

## Summary

Unchain already supports one-shot subagent collaboration through `delegate_to_subagent`, `handoff_to_subagent`, and `spawn_worker_batch`. That covers simple delegation, final handoff, and parallel fan-out/fan-in, but it does not let agents communicate over time.

This design adds a first communication layer for multi-agent work:

1. Persistent agent threads.
2. Direct mailbox messages between agents.
3. A shared blackboard for structured findings and decisions.
4. Return handoff, where a child agent temporarily takes control and then returns the conversation to its parent.

The goal is to upgrade subagents from "tool calls that run to completion" into addressable runtime participants, while keeping the current subagent tools backward-compatible.

## Goals

- Add durable in-run identities for spawned agent threads.
- Let a parent send follow-up messages to a child without respawning it.
- Let agents exchange direct messages through a controlled mailbox.
- Let multiple agents contribute structured findings to a shared board without forcing every interaction through chat transcripts.
- Let a child agent take temporary control and return a result to the parent.
- Preserve existing `delegate_to_subagent`, `handoff_to_subagent`, and `spawn_worker_batch` behavior as compatibility recipes.
- Keep the first implementation in-process and deterministic.
- Reuse existing callback event plumbing and `RunState.subagent_state`.
- Keep policy limits explicit so broad delegation cannot fan out without bounds.

## Non-Goals

- Do not add remote agents, tmux teammates, or multi-process orchestration in this phase.
- Do not add worktree isolation in this phase.
- Do not build debate, quorum, voting, reviewer chains, or batch recipes yet. Those should be implemented later on top of the communication primitives.
- Do not expose arbitrary cross-agent memory sharing.
- Do not allow children to ask the user directly. Clarifications still route through the parent/coordinator.
- Do not replace existing runtime tools in one step.

## Current Behavior

The current public subagent surface is registered by `SubagentModule`:

- `delegate_to_subagent`: spawn a child, run it to completion, return a tool result.
- `handoff_to_subagent`: run a child with optional carried context, then complete the root run with the child answer.
- `spawn_worker_batch`: allocate multiple children, run them through `SubagentExecutor`, and aggregate results in input order.

`SubagentState` tracks lineage counters, active agent id, handoff stack, running batches, blocked clarifications, and spawn stats. It does not track open agent threads, message queues, or blackboard items.

## Proposed Architecture

```text
SubagentModule
  -> existing tools:
       delegate_to_subagent
       handoff_to_subagent
       spawn_worker_batch
  -> new communication tools:
       spawn_agent_thread
       send_agent_message
       wait_agent_messages
       close_agent_thread
       write_agent_board
       read_agent_board
       return_to_parent

SubagentToolPlugin / AgentCommunicationRuntime
  -> thread registry
  -> mailbox queues
  -> blackboard store
  -> return-handoff stack
  -> callback events
```

The first implementation should keep the communication runtime inside the subagents package. It can live in `src/unchain/subagents/communication.py` or a small set of sibling modules. `SubagentToolPlugin` should delegate communication-specific work to that runtime instead of growing into a larger monolith.

Existing tools become recipes over the new primitives:

- `delegate_to_subagent` = spawn thread, send task, wait for final result, optionally close.
- `spawn_worker_batch` = spawn N threads, send N tasks, wait for all, aggregate.
- `handoff_to_subagent` = spawn thread with carried context and terminal transfer semantics.
- `return_handoff` = spawn thread with carried context, wait for a child return message, resume parent.

## Core Data Model

### AgentThreadRecord

Tracks an addressable child agent thread.

Suggested fields:

```python
@dataclass
class AgentThreadRecord:
    thread_id: str
    agent_id: str
    parent_agent_id: str
    template_name: str | None
    mode: str
    status: str
    session_id: str
    memory_namespace: str
    lineage: list[str]
    created_iteration: int
    last_activity_iteration: int
    context_mode: str
    close_reason: str = ""
```

Suggested statuses:

- `pending`
- `running`
- `idle`
- `blocked`
- `completed`
- `failed`
- `closed`

The first implementation can store these records in `SubagentState` as plain serializable dicts, then add typed dataclasses once the shape stabilizes.

### AgentMessage

Represents direct agent-to-agent communication.

Suggested fields:

```python
@dataclass
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
```

Suggested message kinds:

- `task`
- `followup`
- `result`
- `clarification`
- `status`
- `handoff_return`
- `control`

Messages should not be appended automatically into every transcript. The runtime decides which messages become model input for the targeted agent.

### BlackboardItem

Represents shared structured coordination state.

Suggested fields:

```python
@dataclass
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
```

Suggested item kinds:

- `finding`
- `evidence`
- `plan`
- `risk`
- `decision`
- `question`
- `summary`

The blackboard should support append-only writes in the first phase. Superseding an item should create a new item with `supersedes_item_id`; it should not mutate or delete the old item.

## Communication Mode 1: Persistent Threads

Persistent threads are the foundation for the other modes. A spawned child receives a stable `thread_id` and can be addressed later.

New tool:

```text
spawn_agent_thread(
  target: string,
  task: string,
  instructions?: string,
  expected_output?: string,
  context_mode?: "none" | "summary" | "last_n" | "full",
  background?: boolean,
  return_mode?: "result" | "return_to_parent" | "terminal_handoff"
)
```

First implementation behavior:

- `background=false` runs synchronously like today's delegate path.
- `background=true` can be accepted into state but may still be implemented as foreground internally until the executor supports true background scheduling.
- The returned payload includes `thread_id`, `agent_id`, `status`, `lineage`, and latest summary.
- Threads remain visible in state until explicitly closed or pruned by policy.

Companion tools:

```text
wait_agent_messages(thread_ids, condition="all_done", timeout_seconds?)
close_agent_thread(thread_id, reason?)
```

## Communication Mode 2: Mailbox

Mailbox enables directed agent-to-agent messages. It is useful for follow-up questions, reviewer feedback, progress updates, and parent-controlled clarification.

New tool:

```text
send_agent_message(
  recipient: string,
  content: string,
  kind?: string,
  thread_id?: string,
  correlation_id?: string,
  requires_ack?: boolean
)
```

Runtime behavior:

- Validate recipient exists and is open.
- Append the message to the recipient inbox.
- Emit `agent_message_sent`.
- If the recipient is idle and the caller waits, run the recipient with the queued message as input.
- Store any reply/result messages in the sender-visible mailbox.

The first version should not allow free-form child-to-child chatter by default. Policy should default to parent-mediated messaging:

- parent -> child: allowed
- child -> parent: allowed
- child -> sibling: denied unless explicitly enabled
- broadcast: denied unless explicitly enabled

This avoids invisible coordination loops.

## Communication Mode 3: Blackboard

Blackboard is for shared state that should not be forced through natural-language conversation. It reduces context pollution and gives the coordinator a structured place to collect evidence.

New tools:

```text
write_agent_board(
  board_id?: string,
  kind: string,
  title: string,
  content: string,
  tags?: list[string],
  confidence?: number,
  refs?: list[string],
  supersedes_item_id?: string
)

read_agent_board(
  board_id?: string,
  kinds?: list[string],
  tags?: list[string],
  author_agent_id?: string,
  limit?: number
)
```

Runtime behavior:

- Writes are append-only.
- Reads are filtered and ordered by creation order.
- Large item content should be previewed in tool results, with full content retained in state if size limits allow.
- Board items should include enough metadata for a coordinator to cite evidence and decide whether a finding is stale.

The blackboard should not be a hidden global memory. It is scoped to a root run unless explicitly persisted by a future memory policy.

## Communication Mode 4: Return Handoff

Current `handoff_to_subagent` completes the root run with the child answer. Return handoff lets a child temporarily take over, do a focused segment, and then return control to the parent.

New behavior can be expressed through `spawn_agent_thread(return_mode="return_to_parent")` or through a dedicated compatibility tool:

```text
return_handoff_to_subagent(
  target: string,
  reason?: string,
  carry_context?: boolean,
  expected_return?: string
)
```

Child-side tool:

```text
return_to_parent(
  summary: string,
  result?: string,
  status?: "completed" | "blocked" | "failed",
  artifacts?: list[dict]
)
```

Runtime behavior:

1. Parent creates a return-handoff frame.
2. Child runs with carried/summarized context.
3. Child calls `return_to_parent` or ends normally.
4. Runtime records a `handoff_return` message.
5. Parent resumes with the child's summary/result as the next model input.

This differs from delegate because the child can operate with handoff-level context and authority for a segment, but control does not permanently transfer.

## Policy Additions

Extend `SubagentPolicy` conservatively:

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

Existing depth and total-subagent limits still apply. Thread limits apply to open records, not total historical spawns.

## Events

Add raw events first, then normalize them later if needed:

- `agent_thread_spawned`
- `agent_thread_started`
- `agent_thread_idle`
- `agent_thread_completed`
- `agent_thread_closed`
- `agent_message_sent`
- `agent_message_received`
- `agent_message_wait_started`
- `agent_message_wait_completed`
- `agent_board_item_written`
- `agent_board_read`
- `subagent_return_handoff_started`
- `subagent_return_handoff_completed`

Events should include `root_run_id`, `thread_id`, `agent_id`, `parent_id`, `lineage`, and `correlation_id` where applicable.

## Error Handling

- Unknown recipient: return a handled tool error and emit no message.
- Closed thread: return a handled tool error.
- Mailbox overflow: reject new messages unless policy later supports dropping old status messages.
- Oversized message: reject with a clear size error.
- Oversized blackboard item: reject in the first implementation; do not silently truncate author intent.
- Wait timeout: return partial statuses and do not mark the run failed.
- Child blocked on clarification: route a single clarification message to the parent and mark the thread `blocked`.
- Return handoff without an active frame: return a handled tool error.
- Recursive return handoff beyond depth limit: reject before spawning.

## Compatibility

Existing tools should remain source-compatible:

- `delegate_to_subagent` keeps returning a `SubagentResult`-shaped payload.
- `spawn_worker_batch` keeps preserving input order.
- `handoff_to_subagent` keeps terminal handoff behavior by default.

Internally, these tools can use the new communication runtime once it exists. Tests should assert that current public behavior does not regress.

## Testing Plan

Add focused tests for:

- `spawn_agent_thread` creates a stable thread record and lineage.
- `send_agent_message` appends to the correct inbox and rejects unknown recipients.
- `wait_agent_messages` returns completed, blocked, and timeout statuses.
- parent-to-child mailbox follow-up runs the existing child thread instead of spawning a new one.
- child-to-sibling messages are denied by default.
- `write_agent_board` appends structured items and preserves order.
- `read_agent_board` filters by kind, tag, and author.
- board item limits reject oversized writes.
- return handoff resumes parent instead of completing the root run directly.
- current `delegate_to_subagent`, `handoff_to_subagent`, and `spawn_worker_batch` tests continue passing.
- callback events include thread/message/board identifiers.
- `SubagentState.copy()`, `to_dict()`, and `from_raw()` preserve new state fields.

## Implementation Sequence

1. Add serializable state fields for `threads`, `mailboxes`, `blackboards`, and `return_handoff_stack`.
2. Add communication runtime helpers that operate on `SubagentState` without changing tool schemas yet.
3. Add `spawn_agent_thread`, `send_agent_message`, `wait_agent_messages`, and `close_agent_thread`.
4. Implement mailbox follow-up for foreground child runs.
5. Add `write_agent_board` and `read_agent_board`.
6. Add return handoff and `return_to_parent`.
7. Refactor existing delegate/batch/handoff paths to call the communication runtime internally.
8. Add event normalization for the new event types only after raw events are stable.

## Open Questions

- Should completed thread records be retained by default for inspection, or pruned at the end of each root run?
- Should blackboard content be exposed to every child automatically, or only when read explicitly through a tool?
- Should `send_agent_message` support broadcast in the first implementation, or should broadcast wait for a later recipe layer?
- Should return handoff use a dedicated public tool, or only `spawn_agent_thread(return_mode="return_to_parent")` plus `return_to_parent`?

Recommended defaults for the first implementation:

- Retain completed thread records during the root run.
- Do not auto-inject blackboard content.
- Defer broadcast.
- Add a dedicated `return_handoff_to_subagent` compatibility tool only if the model struggles to use `spawn_agent_thread(return_mode="return_to_parent")` reliably.
