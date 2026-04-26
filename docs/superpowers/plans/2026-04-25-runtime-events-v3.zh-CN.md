# Runtime Events v3 实施计划

> **给 agentic workers 的要求：** 执行本计划时必须使用 `superpowers:executing-plans`，逐项完成 checklist。所有步骤使用 checkbox（`- [ ]`）格式，方便追踪。
>
> **用户要求：** 开始 implementation 前必须创建新 branch。默认 branch 名为 `feature/runtime-events-v3`，除非用户指定其他名称。
>
> **用户偏好：** 不要替用户执行 `git commit`。测试通过后停在 dirty working tree 状态，由用户手动 commit。

**目标：** 新增一个最小可用的 typed runtime event stream，让 PuPu 能清晰展示 agent 活动树，同时保留现有 TraceChain 的视觉呈现。第一版必须避免大范围重写 `KernelLoop` / provider，并且必须保持 `/chat/stream/v2` 可用。

**核心决策：** v3 第一版先作为 event bridge 和 frontend reducer，不作为完整 runtime rewrite。

```text
现有 unchain raw event dict
  -> RuntimeEventBridge / RuntimeEventNormalizer
  -> /chat/stream/v3 SSE runtime_event
  -> Electron startStreamV3
  -> PuPu RuntimeEvent store
  -> ActivityTree reducer
  -> TraceChain compatibility props
  -> 现有 TraceChain 视觉组件
```

**第一版不做：**
- 不重新设计 TraceChain 视觉。
- 不移除 `/chat/stream/v2`。
- 不一次性把所有 provider / tool / subagent emitter 改成 typed constructor。
- 不实现 `team.*`、`channel.*`、`plan.*`、`artifact.*` 事件。
- 不新增第三方依赖。

**为未来预留：**

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

## 涉及仓库

本计划横跨两个本地仓库：

| 仓库 | 路径 | 职责 |
|------|------|------|
| unchain | `/Users/red/Desktop/GITRepo/unchain` | Runtime event dataclass、normalizer、测试 |
| PuPu | `/Users/red/Desktop/GITRepo/PuPu` | server route、Electron stream bridge、frontend reducer、TraceChain adapter、debug/testing |

计划编写时，`unchain` 位于 `dev` 分支且工作区干净。`PuPu` 位于 `dev` 分支，但已有用户改动：`README.md`、`package.json`、`package-lock.json`。除非用户明确要求，不要覆盖、回退或格式化这些文件。

---

## Event Protocol v3

所有 runtime event 使用统一 envelope：

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

第一版事件类型：

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

`visibility` 可选值：

```text
user
debug
internal
```

传输层事件：

```text
SSE event: runtime_event
SSE event: done
SSE event: error
```

`done` 和 `error` 是传输生命周期事件，不是 ActivityTree 节点。

---

## Implementation Preflight

- [ ] 在 `unchain` 检查当前状态：

```bash
cd /Users/red/Desktop/GITRepo/unchain
git status --short
git branch --show-current
```

- [ ] 在 `unchain` 创建 implementation branch：

```bash
git switch -c feature/runtime-events-v3
```

- [ ] 在 `PuPu` 检查当前状态：

```bash
cd /Users/red/Desktop/GITRepo/PuPu
git status --short
git branch --show-current
```

- [ ] 如果 PuPu 仍有无关 dirty files，不要修改它们。只有在确认用户接受把这些 dirty changes 带到新 branch 后，再创建 branch：

```bash
git switch -c feature/runtime-events-v3
```

- [ ] 修改任意已有 function / class / method 前，先遵守对应仓库的 `AGENTS.md`。如果 GitNexus MCP 可用，先对目标 symbol 做 impact analysis，并向用户报告 direct callers 和风险等级。如果 GitNexus 不可用，明确说明，并用本地 `rg`、定向阅读和测试作为 fallback。

---

## Phase 1：unchain RuntimeEvent 类型

**目的：** 创建 typed event model，但不改变 `KernelLoop` 行为。

**文件：**

| 路径 | 改动 |
|------|------|
| `src/unchain/events/__init__.py` | 新增 public exports |
| `src/unchain/events/types.py` | 新增 dataclasses 和 literals |
| `tests/test_runtime_events_types.py` | 新增测试 |

- [ ] 先在 `tests/test_runtime_events_types.py` 写失败测试。

测试点：
- `RuntimeEventLinks()` 会序列化所有预留 link key，默认值为 `None`。
- `RuntimeEvent.to_dict()` 返回 JSON-safe data。
- `RuntimeEvent.from_dict()` 可以 round-trip 合法 v3 event。
- 非法 `schema_version` 抛出 `ValueError`。
- unknown event type 只在 `strict=False` 时允许；strict mode 下抛错。

- [ ] 实现 `src/unchain/events/types.py`。

建议 API：

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

- [ ] 从 `src/unchain/events/__init__.py` 导出。

- [ ] 运行测试：

```bash
cd /Users/red/Desktop/GITRepo/unchain
PYTHONPATH=src pytest tests/test_runtime_events_types.py -q
```

---

## Phase 2：unchain RuntimeEvent Normalizer

**目的：** 把当前 raw event dict 转成 v3 RuntimeEvent，而不是立刻重写所有 emitter。

**文件：**

| 路径 | 改动 |
|------|------|
| `src/unchain/events/normalizer.py` | 新增 raw-event mapping 逻辑 |
| `src/unchain/events/bridge.py` | 新增 stateful bridge，支持 id/clock injection |
| `tests/test_runtime_events_normalizer.py` | 新增测试 |
| `tests/test_runtime_events_bridge.py` | 新增测试 |

- [ ] 先写 raw event mapping 的失败测试。

必须支持的映射：

| Raw event | v3 event |
|----------|----------|
| `run_started` | `run.started` |
| `run_completed` | `run.completed` |
| route 捕获的 raw exception | `run.failed` |
| `iteration_started` | `turn.started` |
| `iteration_completed` | `turn.completed` |
| `request_messages` | `model.started`，`visibility="debug"` |
| `token_delta` | `model.delta`，`payload.kind="text"` |
| `reasoning` | `model.delta`，`payload.kind="reasoning"` |
| `response_received` | `model.completed` |
| `final_message` | 如果没有后续 `response_received` 数据，则可映射为 `model.completed` |
| `tool_call` | `tool.started` |
| `tool_result` | `tool.completed` |
| `tool_confirmed` | `input.resolved`，`decision="approved"` |
| `tool_denied` | `input.resolved`，`decision="denied"` |
| `human_input_requested` | `input.requested` |
| `subagent_started` | child `run.started` |
| `subagent_completed` | child `run.completed` |
| `subagent_failed` | child `run.failed` |

- [ ] unknown raw event 默认 drop，并记录到 bridge diagnostics。

- [ ] 实现 `RuntimeEventBridge`。

建议 constructor：

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

建议 methods：

```python
emit_session_started(payload: dict[str, Any] | None = None) -> RuntimeEvent
normalize(raw_event: dict[str, Any]) -> list[RuntimeEvent]
emit_transport_failure(message: str, code: str = "stream_failed") -> RuntimeEvent
diagnostics() -> dict[str, Any]
```

- [ ] subagent links 必须显式：

```python
links.parent_run_id = raw_event.get("root_run_id") or bridge.root_run_id
run_id = raw_event["child_run_id"]
agent_id = raw_event["subagent_id"]
payload.mode = raw_event["mode"]
payload.template = raw_event["template"]
payload.lineage = raw_event["lineage"]
```

- [ ] 运行测试：

```bash
cd /Users/red/Desktop/GITRepo/unchain
PYTHONPATH=src pytest tests/test_runtime_events_normalizer.py tests/test_runtime_events_bridge.py -q
```

---

## Phase 3：PuPu `/chat/stream/v3`

**目的：** 通过 SSE 暴露 v3，同时保持 `/chat/stream/v2` 不变。

**文件：**

| 路径 | 改动 |
|------|------|
| `/Users/red/Desktop/GITRepo/PuPu/unchain_runtime/server/route_chat.py` | 新增 `/chat/stream/v3` |
| `/Users/red/Desktop/GITRepo/PuPu/unchain_runtime/server/unchain_adapter.py` | 只在必要时新增 v3 callback event hooks |
| `/Users/red/Desktop/GITRepo/PuPu/unchain_runtime/server/tests/test_chat_stream_v3.py` | 新增测试 |

- [ ] 先新增 `test_chat_stream_v3.py`。

测试点：
- 空 message 且无 attachments 时，返回和 v2 一致的 400。
- route 输出 `event: runtime_event`，且包含 `schema_version: "v3"`。
- 初始 stream 输出 `session.started`。
- mock raw `run_started`、`token_delta`、`tool_call`、`tool_result`、`final_message`、`run_completed` 会转换成 v3 events。
- route 在最后一个 runtime event 后输出 transport `done`。
- generator exception 会输出 v3 `run.failed`，并一致地输出 transport `done` 或 `error`。
- `/chat/stream/v2` 现有测试继续通过。

- [ ] 实现 `/chat/stream/v3`。

Route 行为：

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

- [ ] 不改 `_build_trace_frame()`，不改 v2 sanitization 行为。

- [ ] 如果 v3 需要 confirmation request events，优先在 PuPu adapter callback 边界补齐，不要塞进 `KernelLoop`：

```text
_make_tool_confirm_callback -> emit v3-compatible raw tool_confirmation_requested/input_requested event
_make_human_input_callback -> already has enough data for input.requested
_make_continuation_callback -> map to input.requested kind="continue"
```

- [ ] 运行测试：

```bash
cd /Users/red/Desktop/GITRepo/PuPu
python -m pytest unchain_runtime/server/tests/test_chat_stream_v3.py -q
python -m pytest unchain_runtime/server/tests/test_models_catalog_route.py -q
```

---

## Phase 4：Electron `startStreamV3`

**目的：** 新增 v3 stream client，不影响 v2 caller。

**文件：**

| 路径 | 改动 |
|------|------|
| `electron/shared/channels.js` | 新增 `STREAM_START_V3` |
| `electron/main/services/unchain/service.js` | 新增 `/chat/stream/v3` endpoint 和 handler |
| `electron/main/ipc/register_handlers.js` | 注册 `STREAM_START_V3` |
| `electron/preload/stream/unchain_stream_client.js` | 新增 `startStreamV3` |
| `electron/preload/bridges/unchain_bridge.js` | expose `startStreamV3` |
| `electron/tests/preload/unchain_stream_client.test.cjs` | 新增测试 |
| `electron/tests/preload/api_contract.test.cjs` | 新增 contract assertion |
| `electron/tests/main/unchain_service.test.cjs` | 新增 endpoint/bridge 测试 |

- [ ] 先写 preload 失败测试。

预期 `startStreamV3` handler API：

```js
api.unchain.startStreamV3(payload, {
  onRuntimeEvent(event) {},
  onDone(data) {},
  onError(error) {},
})
```

- [ ] 实现 `registerMisoStreamV3Listener`。

行为：
- `eventName === "runtime_event"` 调用 `handlers.onRuntimeEvent(data)`。
- `eventName === "done"` 调用 `handlers.onDone(data)` 并 cleanup。
- `eventName === "error"` 调用 `handlers.onError(...)` 并 cleanup。
- v3 listener 忽略 v2 `frame` events。

- [ ] 新增 `UNCHAIN_STREAM_V3_ENDPOINT = "/chat/stream/v3"` 和 `startMisoStreamV3`。

- [ ] 运行测试：

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- electron/tests/preload/unchain_stream_client.test.cjs
npm test -- electron/tests/preload/api_contract.test.cjs
npm test -- electron/tests/main/unchain_service.test.cjs
```

---

## Phase 5：PuPu RuntimeEvent Store 和 ActivityTree Reducer

**目的：** 先把 v3 frontend state 显式建模，再接入 chat。

**文件：**

| 路径 | 改动 |
|------|------|
| `src/SERVICEs/runtime_events/event_store.js` | 新增纯 event store helper |
| `src/SERVICEs/runtime_events/activity_tree.js` | 新增 reducer |
| `src/SERVICEs/runtime_events/trace_chain_adapter.js` | ActivityTree -> TraceChain props |
| `src/SERVICEs/runtime_events/activity_tree.test.js` | 新增测试 |
| `src/SERVICEs/runtime_events/trace_chain_adapter.test.js` | 新增测试 |

- [ ] 先写 reducer 失败测试。

Reducer 测试点：
- 单 run 加 `model.delta` 会产生 streaming content 和一个 root activity。
- `tool.started` + `tool.completed` 会产生 tool node，并输出 v2-compatible `tool_call` / `tool_result` props。
- `input.requested` + `input.resolved` 会产生 confirmation UI state。
- child `run.started` 加 `links.parent_run_id` 会嵌套到 parent run 下。
- child tool events 会路由到 `subagentFrames[childRunId]`。
- `run.failed` 会把状态标为 `error`。
- unknown event type 会进入 diagnostics，不参与 render，也不 crash。

- [ ] 实现 reducer state shape。

建议结构：

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

- [ ] 实现 TraceChain adapter。

Adapter 输出必须匹配现有 TraceChain props：

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

映射到 compatibility frames：

| v3 event | TraceChain prop/frame |
|----------|-----------------------|
| `model.delta` kind `reasoning` | `reasoning` frame |
| `model.delta` kind `text` | `streamingContent` |
| `tool.started` | `tool_call` frame |
| `tool.completed` | `tool_result` frame |
| `input.requested` | enrich matching `tool_call` with `confirmation_id`、`interact_type`、`interact_config` |
| `input.resolved` approved | `tool_confirmed` frame + UI state |
| `input.resolved` denied | `tool_denied` frame + UI state |
| child `run.*` | `subagentMetaByRunId` |
| events with child `run_id` | `subagentFrames[run_id]` |

- [ ] 本阶段不修改 `src/COMPONENTs/chat-bubble/trace_chain.js`。

- [ ] 运行测试：

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- src/SERVICEs/runtime_events/activity_tree.test.js
npm test -- src/SERVICEs/runtime_events/trace_chain_adapter.test.js
```

---

## Phase 6：UI Testing 和 Debug Hooks

**目的：** 利用 PuPu 现有 debug 基础设施，验证 v3 的视觉和自动化可测性。

**文件：**

| 路径 | 改动 |
|------|------|
| `src/COMPONENTs/ui-testing/scenarios/trace_chain_scenarios.js` | 新增 v3 scenarios，不删除 v2 scenarios |
| `src/COMPONENTs/ui-testing/runners/trace_chain_runner.js` | 允许 scenario 提供 `frames` 或 `events` |
| `src/SERVICEs/test_bridge/handlers/debug.js` | 新增 v3 debug handlers |
| `src/SERVICEs/test_bridge/state_selector.js` | 如可用，包含 v3 diagnostics |
| `electron/main/services/test-api/builtin_commands.js` | 新增 debug routes |

- [ ] 新增 v3 scenarios：

```text
V3 Basic Flow
V3 Tool Approval
V3 Human Input Selection
V3 Delegate Subagent
V3 Worker Batch
V3 Run Failure
```

- [ ] 更新 `TraceChainRunner`：

```text
if scenario.frames exists:
    existing behavior unchanged
if scenario.events exists:
    replay RuntimeEvent[] through ActivityTree reducer
    pass trace_chain_adapter output into TraceChain
```

- [ ] 新增 renderer debug handlers：

```text
getRuntimeEvents
getActivityTree
getRuntimeEventDiagnostics
replayRuntimeEvents
clearRuntimeEvents
```

- [ ] 新增 test API routes：

```text
GET  /v1/debug/runtime-events
GET  /v1/debug/activity-tree
POST /v1/debug/runtime-events/replay
POST /v1/debug/runtime-events/clear
```

- [ ] console logger 要克制使用：

```text
runtime_event: only run/tool/input/model lifecycle
runtime_event_delta: disabled by default for model.delta
activity_tree: reducer warnings and unknown events
```

- [ ] 运行测试：

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- src/SERVICEs/test_bridge/state_selector.test.js
npm test -- src/COMPONENTs/chat-bubble/trace_chain.test.js
```

---

## Phase 7：通过 Feature Flag 接入真实 Chat

**目的：** 把 v3 接入真实 chat path，但不冒险影响当前 v2 体验。

**文件：**

| 路径 | 改动 |
|------|------|
| `src/SERVICEs/feature_flags.js` | 新增 v3 stream feature flag |
| `src/PAGEs/chat/hooks/use_chat_stream.js` | 在 v2 path 旁边新增 v3 path |
| `src/PAGEs/chat/hooks/use_chat_stream.frame_coalesce.test.js` | 保持通过 |
| `src/PAGEs/chat/hooks/use_chat_stream.runtime_events.test.js` | 新增测试 |
| `src/COMPONENTs/chat-bubble/chat_bubble.js` | 仅在必要时做最小 prop handoff |
| `src/COMPONENTs/chat-bubble/character_chat_bubble.js` | 同上，仅在必要时修改 |

- [ ] 新增 feature flag：

```js
enable_runtime_events_v3: false
```

- [ ] 新增 v3 message update 行为测试。

必须满足：
- v2 仍然是默认路径。
- flag 关闭时，仍调用 `startStreamV2`。
- flag 开启时，调用 `startStreamV3`。
- `model.delta` text 更新 assistant content。
- `tool.started/completed` 通过 adapter 更新 trace frames。
- `input.requested` 显示现有 InteractWrapper UI。
- `run.completed` finalize assistant message。
- `run.failed` surface stream error 和 trace error。

- [ ] 在 `use_chat_stream.js` 做最小 branching。

建议模式：

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

- [ ] 保持现有 v2 confirmation、continuation、subagent 行为不变。

- [ ] 运行测试：

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- src/PAGEs/chat/hooks/use_chat_stream.runtime_events.test.js
npm test -- src/PAGEs/chat/hooks/use_chat_stream.frame_coalesce.test.js
npm test -- src/COMPONENTs/chat-bubble/chat_bubble.test.js
```

---

## Phase 8：端到端验证

- [ ] 运行 unchain focused tests：

```bash
cd /Users/red/Desktop/GITRepo/unchain
PYTHONPATH=src pytest \
  tests/test_runtime_events_types.py \
  tests/test_runtime_events_normalizer.py \
  tests/test_runtime_events_bridge.py \
  -q
```

- [ ] 时间允许时运行 unchain 全量测试：

```bash
cd /Users/red/Desktop/GITRepo/unchain
PYTHONPATH=src pytest tests/ -q
```

项目说明中已知 flaky tests：

```text
test_read_file_ast_parses_python_file
test_pinned_prompt_messages_relocate_non_python_ranges_via_declaration_metadata
```

- [ ] 运行 PuPu focused tests：

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- electron/tests/preload/unchain_stream_client.test.cjs
npm test -- electron/tests/preload/api_contract.test.cjs
npm test -- src/SERVICEs/runtime_events/activity_tree.test.js
npm test -- src/SERVICEs/runtime_events/trace_chain_adapter.test.js
npm test -- src/COMPONENTs/chat-bubble/trace_chain.test.js
python -m pytest unchain_runtime/server/tests/test_chat_stream_v3.py -q
```

- [ ] 时间允许时运行 PuPu 更广测试：

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm test -- --watchAll=false
python -m pytest unchain_runtime/server/tests -q
```

- [ ] 手动视觉检查：

```bash
cd /Users/red/Desktop/GITRepo/PuPu
npm start
```

打开 UI testing modal，确认：
- v2 TraceChain scenarios 视觉不变。
- v3 scenarios 通过 ActivityTree 和现有 TraceChain visual 渲染。
- subagent nested traces 更清晰，但没有被重新设计。
- approval 和 code diff interaction 仍使用现有 InteractWrapper components。

- [ ] 如果 Browser Use 可用，且 app 暴露本地 browser target，则为以下场景截图：

```text
V3 Basic Flow
V3 Tool Approval
V3 Delegate Subagent
V3 Worker Batch
```

---

## 验收标准

- [ ] `/chat/stream/v2` 不变，现有 v2 测试通过。
- [ ] `/chat/stream/v3` 输出 typed RuntimeEvent envelope。
- [ ] v3 第一版只支持本计划列出的第一批事件。
- [ ] PuPu 能把 v3 events reduce 成 ActivityTree。
- [ ] 现有 TraceChain 视觉组件保持 intact。
- [ ] v3 UI path 放在 feature flag 后。
- [ ] Test API 可以检查 runtime events 和 ActivityTree state。
- [ ] unknown raw events 不会 crash frontend。
- [ ] child runs 使用 `links.parent_run_id`，不再依赖 v2 subagent result guessing。
- [ ] 不实现 team/channel/plan UI，但在 `links` 中预留 IDs。

---

## 后续计划

v3 第一版稳定后，再做：

1. 把 raw `KernelLoop.emit_event()` call sites 替换为 typed constructors。
2. 新增 PermissionPolicyModule 和 SandboxPolicyModule events。
3. 等 agent-to-agent messaging 存在后，再加 `channel.*`。
4. 等 planner/task state 具体化后，再加 `plan.*`。
5. 当 files、diffs、screenshots、generated assets 需要 first-class lifecycle 时，再加 `artifact.*`。
6. v3 默认启用足够久以后，再移除 v2-only TraceChain data inference。
