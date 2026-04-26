# Runtime Events v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **User requirement:** before starting implementation, create a new branch. Use `feature/runtime-events-v3` unless the user asks for another branch name.
>
> **User preference:** do not run `git commit` on the user's behalf. Stop with a dirty working tree after tests pass; the user commits manually.

**Goal:** Add a minimal typed runtime event stream that lets PuPu show a clear agent activity tree while preserving the existing TraceChain visual design. The first version must avoid a broad KernelLoop/provider rewrite and must keep `/chat/stream/v2` working.

**Core decision:** v3 starts as an event bridge and reducer, not a full runtime rewrite.

```text
existing unchain raw event dicts
  -> RuntimeEventBridge / RuntimeEventNormalizer
  -> /chat/stream/v3 SSE runtime_event
  -> Electron startStreamV3
  -> PuPu RuntimeEvent store
  -> ActivityTree reducer
  -> TraceChain compatibility props
  -> existing TraceChain visual components
```

**Non-goals for v3 first pass:**
- Do not redesign TraceChain visuals.
- Do not remove `/chat/stream/v2`.
- Do not replace every provider/tool/subagent emitter with typed constructors yet.
- Do not implement `team.*`, `channel.*`, `plan.*`, or `artifact.*` events yet.
- Do not add new third-party dependencies.

**Reserved future expansion:**

```json
{
  "links": {
    "team_id": null,
    "channel_id": null,
    "plan_id": null
  }
}
```

---

## Repositories

This plan spans two local repositories:

| Repo | Path | Role |
|------|------|------|
| unchain | `/Users/red/Desktop/GITRepo/unchain` | Runtime event dataclasses, normalizer, tests |
| PuPu | `/Users/red/Desktop/GITRepo/PuPu` | Server route, Electron stream bridge, frontend reducer, TraceChain adapter, debug/testing |

At planning time, `unchain` was clean on `dev`. `PuPu` had existing user changes in `README.md`, `package.json`, and `package-lock.json`. Do not overwrite or revert those files unless the user explicitly asks.

---

## Event Protocol v3

All runtime events use this envelope:

```json
{
  "schema_version": "v3",
  "event_id": "evt_...",
  "type": "domain.action",
  "timestamp": "2026-04-25T12:34:56.789Z",
  "session_id": "thread-...",
  "run_id": "run-...",
  "agent_id": "developer",
  "turn_id": "turn-...",
  "links": {
    "parent_run_id": null,
    "parent_event_id": null,
    "caused_by_event_id": null,
    "tool_call_id": null,
    "input_request_id": null,
    "channel_id": null,
    "team_id": null,
    "plan_id": null
  },
  "visibility": "user",
  "payload": {},
  "metadata": {}
}
```

First-pass event types:

```text
session.started

run.started
run.completed
run.failed

turn.started
turn.completed

model.started
model.delta
model.completed

tool.started
tool.delta
tool.completed

input.requested
input.resolved
```

Visibility values:

```text
user
debug
internal
```

Transport events:

```text
SSE event: runtime_event
SSE event: done
SSE event: error
```

`done` and `error` are transport lifecycle events, not ActivityTree nodes.

---

## Implementation Preflight

- [ ] In `unchain`, check status:

```bash
cd /Users/red/Desktop/GITRepo/unchain
git status --short
git branch --show-current
```

- [ ] Create implementation branch in `unchain`:

```bash
git switch -c feature/runtime-events-v3
```

- [ ] In `PuPu`, check status:

```bash
cd /Users/red/Desktop/GITRepo/PuPu
git status --short
git branch --show-current
```

- [ ] If PuPu still has unrelated dirty files, do not modify them. Create the branch only after confirming the user is okay carrying those dirty changes onto the new branch:

```bash
git switch -c feature/runtime-events-v3
```

- [ ] Before editing any existing function/class/method in either repo, follow that repo's `AGENTS.md`. If GitNexus MCP tools are available, run impact analysis for the target symbol and report direct callers and risk. If GitNexus is unavailable, state that explicitly and use local `rg`, focused reads, and tests as the fallback.

---

## Phase 1: unchain RuntimeEvent Types

**Purpose:** create a typed event model without touching KernelLoop behavior.

**Files:**

| Path | Change |
|------|--------|
| `src/unchain/events/__init__.py` | New public exports |
| `src/unchain/events/types.py` | New dataclasses and literals |
| `tests/test_runtime_events_types.py` | New tests |

- [ ] Create failing tests in `tests/test_runtime_events_types.py`.

Test cases:
- `RuntimeEventLinks()` serializes all reserved link keys with `None`.
- `RuntimeEvent.to_dict()` returns JSON-safe data.
- `RuntimeEvent.from_dict()` round-trips valid v3 events.
- Invalid `schema_version` raises `ValueError`.
- Unknown event type is accepted only if `strict=False`; strict mode raises.

- [ ] Implement `src/unchain/events/types.py`.

Recommended API:

```python
RuntimeEventType = Literal[
    "session.started",
    "run.started",
    "run.completed",
    "run.failed",
    "turn.started",
    "turn.completed",
    "model.started",
    "model.delta",
    "model.completed",
    "tool.started",
    "tool.delta",
    "tool.completed",
    "input.requested",
    "input.resolved",
]

Visibility = Literal["user", "debug", "internal"]

@dataclass(frozen=True)
class RuntimeEventLinks:
    parent_run_id: str | None = None
    parent_event_id: str | None = None
    caused_by_event_id: str | None = None
    tool_call_id: str | None = None
    input_request_id: str | None = None
    channel_id: str | None = None
    team_id: str | None = None
    plan_id: str | None = None

@dataclass(frozen=True)
class RuntimeEvent:
    schema_version: str
    event_id: str
    type: str
    timestamp: str
    session_id: str
    run_id: str
    agent_id: str
    turn_id: str | None
    links: RuntimeEventLinks
    visibility: Visibility
    payload: dict[str, Any]
    metadata: dict[str, Any]
```

- [ ] Export from `src/unchain/events/__init__.py`.

- [ ] Run:

```bash
cd /Users/red/Desktop/GITRepo/unchain
PYTHONPATH=src pytest tests/test_runtime_events_types.py -q
```

---

## Phase 2: unchain RuntimeEvent Normalizer

**Purpose:** convert current raw event dicts into v3 RuntimeEvent without rewriting all emitters.

**Files:**

| Path | Change |
|------|--------|
| `src/unchain/events/normalizer.py` | New raw-event mapping logic |
| `src/unchain/events/bridge.py` | New stateful bridge with id/clock injection |
| `tests/test_runtime_events_normalizer.py` | New tests |
| `tests/test_runtime_events_bridge.py` | New tests |

- [ ] Create failing tests for raw event mapping.

Required mapping:

| Raw event | v3 event |
|----------|----------|
| `run_started` | `run.started` |
| `run_completed` | `run.completed` |
| raw thrown error from route | `run.failed` |
| `iteration_started` | `turn.started` |
| `iteration_completed` | `turn.completed` |
| `request_messages` | `model.started` with `visibility="debug"` |
| `token_delta` | `model.delta` with `payload.kind="text"` |
| `reasoning` | `model.delta` with `payload.kind="reasoning"` |
| `response_received` | `model.completed` |
| `final_message` | `model.completed` if no later `response_received` data is available |
| `tool_call` | `tool.started` |
| `tool_result` | `tool.completed` |
| `tool_confirmed` | `input.resolved` with `decision="approved"` |
| `tool_denied` | `input.resolved` with `decision="denied"` |
| `human_input_requested` | `input.requested` |
| `subagent_started` | child `run.started` |
| `subagent_completed` | child `run.completed` |
| `subagent_failed` | child `run.failed` |

- [ ] Unknown raw events should be dropped by default and collected in bridge diagnostics.

- [ ] Implement `RuntimeEventBridge`.

Recommended constructor:

```python
RuntimeEventBridge(
    *,
    session_id: str,
    root_run_id: str | None = None,
    root_agent_id: str = "developer",
    id_factory: Callable[[], str] | None = None,
    clock: Callable[[], datetime] | None = None,
    trace_level: str = "minimal",
)
```

Recommended methods:

```python
emit_session_started(payload: dict[str, Any] | None = None) -> RuntimeEvent
normalize(raw_event: dict[str, Any]) -> list[RuntimeEvent]
emit_transport_failure(message: str, code: str = "stream_failed") -> RuntimeEvent
diagnostics() -> dict[str, Any]
```

- [ ] Ensure subagent links are explicit:

```python
links.parent_run_id = raw_event.get("root_run_id") or bridge.root_run_id
run_id = raw_event["child_run_id"]
agent_id = raw_event["subagent_id"]
payload.mode = raw_event["mode"]
payload.template = raw_event["template"]
payload.lineage = raw_event["lineage"]
```

- [ ] Run:

```bash
cd /Users/red/Desktop/GITRepo/unchain
PYTHONPATH=src pytest tests/test_runtime_events_normalizer.py tests/test_runtime_events_bridge.py -q
```

---

## Phase 3: PuPu `/chat/stream/v3`

**Purpose:** expose v3 over SSE while leaving `/chat/stream/v2` unchanged.

**Files:**

| Path | Change |
|------|--------|
| `/Users/red/Desktop/GITRepo/PuPu/unchain_runtime/server/route_chat.py` | Add `/chat/stream/v3` |
| `/Users/red/Desktop/GITRepo/PuPu/unchain_runtime/server/unchain_adapter.py` | Add v3 callback event hooks only if needed |
| `/Users/red/Desktop/GITRepo/PuPu/unchain_runtime/server/tests/test_chat_stream_v3.py` | New tests |

- [ ] Add tests first in `test_chat_stream_v3.py`.

Test cases:
- Empty message and no attachments returns same 400 behavior as v2.
- Route emits `event: runtime_event` with `schema_version: "v3"`.
- Initial stream emits `session.started`.
- Mock raw `run_started`, `token_delta`, `tool_call`, `tool_result`, `final_message`, `run_completed` become v3 events.
- Route emits transport `done` after the last runtime event.
- Generator exception emits v3 `run.failed` and transport `done` or `error` consistently.
- `/chat/stream/v2` tests continue passing.

- [ ] Implement `/chat/stream/v3`.

Route behavior:

```text
POST /chat/stream/v3
  validate authorization
  sanitize message/history/options like v2
  create RuntimeEventBridge(session_id=thread_id)
  emit session.started as runtime_event
  for raw_event in root.stream_chat_events(...):
      for runtime_event in bridge.normalize(raw_event):
          yield event: runtime_event
  yield event: done
```

- [ ] Do not change `_build_trace_frame()` or v2 sanitization behavior.

- [ ] If v3 needs confirmation request events, add them at the PuPu adapter callback boundary first, not inside KernelLoop:

```text
_make_tool_confirm_callback -> emit v3-compatible raw tool_confirmation_requested/input_requested event
_make_human_input_callback -> already has enough data for input.requested
_make_continuation_callback -> map to input.requested kind="continue"
```

- [ ] Run:

```bash
cd /Users/red/Desktop/GITRepo/PuPu
python -m pytest unchain_runtime/server/tests/test_chat_stream_v3.py -q
python -m pytest unchain_runtime/server/tests/test_models_catalog_route.py -q
```

---

## Phase 4: Electron `startStreamV3`

**Purpose:** add a v3 stream client without changing v2 callers.

**Files:**

| Path | Change |
|------|--------|
| `electron/shared/channels.js` | Add `STREAM_START_V3` |
| `electron/main/services/unchain/service.js` | Add `/chat/stream/v3` endpoint and handler |
| `electron/main/ipc/register_handlers.js` | Register `STREAM_START_V3` |
| `electron/preload/stream/unchain_stream_client.js` | Add `startStreamV3` |
| `electron/preload/bridges/unchain_bridge.js` | Expose `startStreamV3` |
| `electron/tests/preload/unchain_stream_client.test.cjs` | Add tests |
| `electron/tests/preload/api_contract.test.cjs` | Add contract assertion |
| `electron/tests/main/unchain_service.test.cjs` | Add endpoint/bridge test |

- [ ] Add failing preload tests.

Expected `startStreamV3` handler API:

```js
api.unchain.startStreamV3(payload, {
  onRuntimeEvent(event) {},
  onDone(data) {},
  onError(error) {},
})
```

- [ ] Implement `registerMisoStreamV3Listener`.

Behavior:
- `eventName === "runtime_event"` calls `handlers.onRuntimeEvent(data)`.
- `eventName === "done"` calls `handlers.onDone(data)` and cleans up.
- `eventName === "error"` calls `handlers.onError(...)` and cleans up.
- Ignore v2 `frame` events in the v3 listener.

- [ ] Add `UNCHAIN_STREAM_V3_ENDPOINT = "/chat/stream/v3"` and `startMisoStreamV3`.

- [ ] Run:

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- electron/tests/preload/unchain_stream_client.test.cjs
npm test -- electron/tests/preload/api_contract.test.cjs
npm test -- electron/tests/main/unchain_service.test.cjs
```

---

## Phase 5: PuPu RuntimeEvent Store and ActivityTree Reducer

**Purpose:** make v3 frontend state explicit before touching chat integration.

**Files:**

| Path | Change |
|------|--------|
| `src/SERVICEs/runtime_events/event_store.js` | New pure event store helpers |
| `src/SERVICEs/runtime_events/activity_tree.js` | New reducer |
| `src/SERVICEs/runtime_events/trace_chain_adapter.js` | Convert ActivityTree to TraceChain props |
| `src/SERVICEs/runtime_events/activity_tree.test.js` | New tests |
| `src/SERVICEs/runtime_events/trace_chain_adapter.test.js` | New tests |

- [ ] Add failing reducer tests.

Reducer test cases:
- Single run with `model.delta` produces streaming content and one root activity.
- `tool.started` + `tool.completed` produce a tool node and v2-compatible `tool_call`/`tool_result` props.
- `input.requested` + `input.resolved` produce confirmation UI state.
- Child `run.started` with `links.parent_run_id` nests under parent run.
- Child tool events route to `subagentFrames[childRunId]`.
- `run.failed` marks status `error`.
- Unknown event type is stored in diagnostics, not rendered.

- [ ] Implement reducer state shape.

Recommended shape:

```js
{
  eventsById: {},
  orderedEventIds: [],
  runsById: {},
  rootRunIds: [],
  toolCallsById: {},
  inputRequestsById: {},
  diagnostics: {
    unknownEvents: [],
    droppedEvents: [],
  },
}
```

- [ ] Implement TraceChain adapter.

Adapter output must match existing TraceChain props:

```js
{
  frames,
  status,
  streamingContent,
  subagentFrames,
  subagentMetaByRunId,
  toolConfirmationUiStateById,
}
```

Mapping to compatibility frames:

| v3 event | TraceChain prop/frame |
|----------|-----------------------|
| `model.delta` kind `reasoning` | `reasoning` frame |
| `model.delta` kind `text` | `streamingContent` |
| `tool.started` | `tool_call` frame |
| `tool.completed` | `tool_result` frame |
| `input.requested` | enrich matching `tool_call` with `confirmation_id`, `interact_type`, `interact_config` |
| `input.resolved` approved | `tool_confirmed` frame + UI state |
| `input.resolved` denied | `tool_denied` frame + UI state |
| child `run.*` | `subagentMetaByRunId` |
| events with child `run_id` | `subagentFrames[run_id]` |

- [ ] Do not modify `src/COMPONENTs/chat-bubble/trace_chain.js` in this phase.

- [ ] Run:

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- src/SERVICEs/runtime_events/activity_tree.test.js
npm test -- src/SERVICEs/runtime_events/trace_chain_adapter.test.js
```

---

## Phase 6: UI Testing and Debug Hooks

**Purpose:** use existing PuPu debug infrastructure to validate v3 visually and automatically.

**Files:**

| Path | Change |
|------|--------|
| `src/COMPONENTs/ui-testing/scenarios/trace_chain_scenarios.js` | Add v3 scenarios without deleting v2 scenarios |
| `src/COMPONENTs/ui-testing/runners/trace_chain_runner.js` | Let scenarios provide either `frames` or `events` |
| `src/SERVICEs/test_bridge/handlers/debug.js` | Add v3 debug handlers |
| `src/SERVICEs/test_bridge/state_selector.js` | Include v3 diagnostics if available |
| `electron/main/services/test-api/builtin_commands.js` | Add debug routes |

- [ ] Add v3 scenarios:

```text
V3 Basic Flow
V3 Tool Approval
V3 Human Input Selection
V3 Delegate Subagent
V3 Worker Batch
V3 Run Failure
```

- [ ] Update `TraceChainRunner`:

```text
if scenario.frames exists:
    existing behavior unchanged
if scenario.events exists:
    replay RuntimeEvent[] through ActivityTree reducer
    pass trace_chain_adapter output into TraceChain
```

- [ ] Add renderer debug handlers:

```text
getRuntimeEvents
getActivityTree
getRuntimeEventDiagnostics
replayRuntimeEvents
clearRuntimeEvents
```

- [ ] Add test API routes:

```text
GET  /v1/debug/runtime-events
GET  /v1/debug/activity-tree
POST /v1/debug/runtime-events/replay
POST /v1/debug/runtime-events/clear
```

- [ ] Ensure console logger is used sparingly:

```text
runtime_event: run/tool/input/model lifecycle only
runtime_event_delta: disabled by default for model.delta
activity_tree: reducer warnings and unknown events
```

- [ ] Run:

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- src/SERVICEs/test_bridge/state_selector.test.js
npm test -- src/COMPONENTs/chat-bubble/trace_chain.test.js
```

---

## Phase 7: Chat Integration Behind a Feature Flag

**Purpose:** wire v3 into the real chat path without risking the current v2 experience.

**Files:**

| Path | Change |
|------|--------|
| `src/SERVICEs/feature_flags.js` | Add v3 stream feature flag |
| `src/PAGEs/chat/hooks/use_chat_stream.js` | Add v3 path beside v2 path |
| `src/PAGEs/chat/hooks/use_chat_stream.frame_coalesce.test.js` | Keep passing |
| `src/PAGEs/chat/hooks/use_chat_stream.runtime_events.test.js` | New tests |
| `src/COMPONENTs/chat-bubble/chat_bubble.js` | Minimal prop handoff only if needed |
| `src/COMPONENTs/chat-bubble/character_chat_bubble.js` | Same as above only if needed |

- [ ] Add feature flag:

```js
enable_runtime_events_v3: false
```

- [ ] Add tests for v3 message update behavior.

Required behavior:
- v2 remains default.
- When flag is off, `startStreamV2` is called exactly as before.
- When flag is on, `startStreamV3` is called.
- `model.delta` text updates assistant content.
- `tool.started/completed` updates trace frames via adapter.
- `input.requested` shows existing InteractWrapper UI.
- `run.completed` finalizes assistant message.
- `run.failed` surfaces stream error and trace error.

- [ ] Integrate in `use_chat_stream.js` with minimal branching.

Recommended pattern:

```js
if (featureFlags.enable_runtime_events_v3) {
  startStreamV3(..., {
    onRuntimeEvent: handleRuntimeEventV3,
    onDone,
    onError,
  });
} else {
  startStreamV2(...existing handlers...);
}
```

- [ ] Keep existing v2 confirmation, continuation, and subagent behavior untouched.

- [ ] Run:

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- src/PAGEs/chat/hooks/use_chat_stream.runtime_events.test.js
npm test -- src/PAGEs/chat/hooks/use_chat_stream.frame_coalesce.test.js
npm test -- src/COMPONENTs/chat-bubble/chat_bubble.test.js
```

---

## Phase 8: End-to-End Verification

- [ ] Run focused unchain tests:

```bash
cd /Users/red/Desktop/GITRepo/unchain
PYTHONPATH=src pytest \
  tests/test_runtime_events_types.py \
  tests/test_runtime_events_normalizer.py \
  tests/test_runtime_events_bridge.py \
  -q
```

- [ ] Run full unchain tests if time allows:

```bash
cd /Users/red/Desktop/GITRepo/unchain
PYTHONPATH=src pytest tests/ -q
```

Known flaky tests from project instructions:

```text
test_read_file_ast_parses_python_file
test_pinned_prompt_messages_relocate_non_python_ranges_via_declaration_metadata
```

- [ ] Run focused PuPu tests:

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- electron/tests/preload/unchain_stream_client.test.cjs
npm test -- electron/tests/preload/api_contract.test.cjs
npm test -- src/SERVICEs/runtime_events/activity_tree.test.js
npm test -- src/SERVICEs/runtime_events/trace_chain_adapter.test.js
npm test -- src/COMPONENTs/chat-bubble/trace_chain.test.js
python -m pytest unchain_runtime/server/tests/test_chat_stream_v3.py -q
```

- [ ] Run PuPu broader tests if time allows:

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- --watchAll=false
python -m pytest unchain_runtime/server/tests -q
```

- [ ] Manual visual check:

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm start
```

Then open UI testing modal and verify:
- v2 TraceChain scenarios still look the same.
- v3 scenarios render through ActivityTree and the existing TraceChain visual.
- Subagent nested traces are clearer but not visually redesigned.
- Approval and code diff interactions still use existing InteractWrapper components.

- [ ] If Browser Use is available and the app exposes a local browser target, capture screenshots for:

```text
V3 Basic Flow
V3 Tool Approval
V3 Delegate Subagent
V3 Worker Batch
```

---

## Acceptance Criteria

- [ ] `/chat/stream/v2` is unchanged and existing v2 tests pass.
- [ ] `/chat/stream/v3` emits typed RuntimeEvent envelopes.
- [ ] v3 supports the first-pass event list only.
- [ ] PuPu can reduce v3 events into an ActivityTree.
- [ ] Existing TraceChain visual components remain intact.
- [ ] v3 UI path is behind a feature flag.
- [ ] Test API can inspect runtime events and ActivityTree state.
- [ ] Unknown raw events do not crash the frontend.
- [ ] Child runs use `links.parent_run_id`, not v2 subagent result guessing.
- [ ] No team/channel/plan UI is implemented, but IDs are reserved in `links`.

---

## Future Follow-up Plan

After v3 first pass is stable:

1. Replace raw `KernelLoop.emit_event()` call sites with typed constructors.
2. Add PermissionPolicyModule and SandboxPolicyModule events.
3. Add `channel.*` once agent-to-agent messaging exists.
4. Add `plan.*` once planner/task state is concrete.
5. Add `artifact.*` when files, diffs, screenshots, and generated assets need first-class lifecycle.
6. Remove v2-only TraceChain data inference after v3 has been default for enough releases.
