# Interaction API Reference

Durable interaction requests, receipts, persistence helpers, synchronous callback
adapters, and the package-level interaction utilities exported by
`unchain.interaction`.

The durable interaction kinds are `human_input`, `tool_approval`, and
`max_budget`.

## Exposure and intended entry points

Application code normally uses [`Agent.submit_interaction()` and
`Agent.resume_interaction()`](agents.md#agent). The immutable data types and typed
errors are also exported from `unchain.interaction`:

```python
from unchain.interaction import (
    InteractionError,
    InteractionReceipt,
    InteractionRequest,
)
```

The persistence facade, snapshot, and max-budget callback adapter are deliberately
not re-exported from `unchain.interaction`:

- `DurableInteractionRuntime` and `DurableInteractionSnapshot` are module-level
  exports from `unchain.interaction.runtime` for framework and storage
  integrations.
- `DurableMaxBudgetCallbackAdapter` is a module-level export from
  `unchain.interaction.adapters`. It is a kernel integration type, not the
  application-facing way to resume an interaction.

## Coverage map

| Class | Source | Exposure | Kind |
| --- | --- | --- | --- |
| `InteractionError` | `src/unchain/interaction/durable.py:36` | `unchain.interaction` | error class |
| `InteractionIntegrityError` | `src/unchain/interaction/durable.py:42` | `unchain.interaction` | error class |
| `InteractionNotPendingError` | `src/unchain/interaction/durable.py:48` | `unchain.interaction` | error class |
| `InteractionReceiptConflictError` | `src/unchain/interaction/durable.py:54` | `unchain.interaction` | error class |
| `InteractionAlreadyAppliedError` | `src/unchain/interaction/durable.py:60` | `unchain.interaction` | error class |
| `InteractionRequest` | `src/unchain/interaction/durable.py:214` | `unchain.interaction` | dataclass (frozen, slots) |
| `InteractionReceipt` | `src/unchain/interaction/durable.py:407` | `unchain.interaction` | dataclass (frozen, slots) |
| `DurableInteractionSnapshot` | `src/unchain/interaction/runtime.py:160` | module-only: `unchain.interaction.runtime` | dataclass (frozen, slots) |
| `DurableInteractionRuntime` | `src/unchain/interaction/runtime.py:205` | module-only: `unchain.interaction.runtime` | dataclass |
| `DurableMaxBudgetCallbackAdapter` | `src/unchain/interaction/adapters.py:31` | module-only: `unchain.interaction.adapters` | dataclass |

## Durable protocol

At a durable suspension, the framework atomically stores an immutable
`InteractionRequest` with the execution checkpoint. A normalized answer is later
stored as a separate `InteractionReceipt` before any resume delta, model call, or
approved tool execution. Applying the receipt is recorded with the next
checkpoint transition or semantic commit.

The request and receipt use canonical JSON and content digests. Re-submitting the
same normalized response is idempotent; a different response for the same request
fails closed. This protects the decision boundary, but it does not make an
arbitrary external tool side effect exactly once.

## InteractionRequest

Immutable, content-addressed description of one pending human-input,
tool-approval, or max-budget decision.

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | `int` | Current durable interaction schema version. |
| `interaction_id` | `str` | Deterministic ID derived from `request_digest`. |
| `session_id` | `str` | Non-empty owning session ID. |
| `kind` | `Literal["human_input", "tool_approval", "max_budget"]` | Response-normalization contract selector. |
| `source_run_id` | `str` | Run that created the request. |
| `occurrence` | `str` | Stable identity for this decision occurrence. |
| `payload` | `Any` | Strict JSON request payload. |
| `response_contract` | `Any` | Strict JSON response contract. |
| `schema_digest` | `str` | Canonical digest of `response_contract`. |
| `request_digest` | `str` | Canonical digest of the request identity fields. |
| `created_revision` | `int` | Non-negative session revision at creation. |
| `subject` | `Any` | Strict JSON execution binding; default `None`. |

Construction and deserialization validate the exact schema, supported kind,
canonical JSON values, digests, and deterministic ID. Unknown or missing fields
in serialized input raise `InteractionIntegrityError`.

### Public methods

| Method | Returns | Description |
| --- | --- | --- |
| `to_dict()` | `dict[str, Any]` | Deep-copy serialization of every schema field. |
| `from_dict(raw)` | `InteractionRequest` | Strictly deserialize and revalidate a request. |

### Builder

```python
build_interaction_request(
    *,
    session_id: str,
    kind: InteractionKind,
    source_run_id: str,
    occurrence: str,
    payload: Any,
    response_contract: Any,
    created_revision: int,
    subject: Any = None,
) -> InteractionRequest
```

`build_interaction_request` is exported from `unchain.interaction` and computes
the schema digest, request digest, and interaction ID from normalized inputs.

## InteractionReceipt

Immutable, content-addressed normalized answer bound to one request.

### Fields

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | `int` | Current durable interaction schema version. |
| `receipt_id` | `str` | Deterministic ID derived from `receipt_digest`. |
| `interaction_id` | `str` | ID of the answered request. |
| `request_digest` | `str` | Digest binding the receipt to that request. |
| `response` | `Any` | Strict JSON normalized response. |
| `response_digest` | `str` | Canonical digest of `response`. |
| `submitted_by` | `str` | Non-empty submitter identity. |
| `receipt_digest` | `str` | Canonical digest of the receipt identity fields. |
| `submitted_at_ms` | `int` | Non-negative observation timestamp in milliseconds. It is metadata and does not participate in `receipt_digest` or `receipt_id`. |

### Public methods

| Method | Returns | Description |
| --- | --- | --- |
| `to_dict()` | `dict[str, Any]` | Deep-copy serialization of every schema field. |
| `from_dict(raw, *, request=None)` | `InteractionReceipt` | Strictly deserialize a receipt and optionally verify that it belongs to `request`. |

### Builder

```python
build_interaction_receipt(
    request: InteractionRequest | dict[str, Any],
    response: Any,
    *,
    submitted_by: str = "user",
    submitted_at_ms: int,
) -> InteractionReceipt
```

`build_interaction_receipt` is exported from `unchain.interaction`. Application
code normally uses `Agent.submit_interaction()` so the response is normalized for
the request kind and persisted with session CAS semantics.

## Interaction errors

All public durable interaction errors inherit from `InteractionError`, which in
turn inherits from `RuntimeError`.

| Error | `code` | Meaning |
| --- | --- | --- |
| `InteractionError` | `interaction_error` | Base class for durable interaction failures. |
| `InteractionIntegrityError` | `interaction_integrity_error` | Persisted data, schema, digest, or execution binding is malformed or inconsistent. |
| `InteractionNotPendingError` | `interaction_not_pending` | The requested interaction is absent, inactive, already consumed, or has no required receipt. |
| `InteractionReceiptConflictError` | `interaction_receipt_conflict` | A different answer was submitted for a request that already has a receipt. |
| `InteractionAlreadyAppliedError` | `interaction_already_applied` | A different application attempts to replace an already-applied receipt. |

## DurableInteractionSnapshot

Read result returned by `DurableInteractionRuntime`. It is exported from
`unchain.interaction.runtime`, not from `unchain.interaction`.

### Fields and properties

| Field/property | Type | Notes |
| --- | --- | --- |
| `request` | `InteractionRequest` | Validated immutable request. |
| `checkpoint_id` | `str` | Checkpoint bound to the request. |
| `receipt` | `InteractionReceipt \| None` | Persisted answer, if submitted. |
| `application` | `dict[str, Any] \| None` | Receipt-application marker, if applied. |
| `session_snapshot` | `SessionSnapshot` | Revisioned session state used for the read. |
| `response` | `dict[str, Any] \| None` | Deep copy of `receipt.response`, or `None`. |

## DurableInteractionRuntime

Persistence facade for loading the journal and recording one normalized response
inside the revisioned session document. It is exported from
`unchain.interaction.runtime`, not from `unchain.interaction`; most callers should
prefer the `Agent` methods.

### Constructor

```python
DurableInteractionRuntime(
    memory_runtime: KernelMemoryRuntime,
    clock_ms: Callable[[], int] = ...,
)
```

### Public methods

| Method | Returns | Description |
| --- | --- | --- |
| `load(session_id, *, interaction_id=None, require_active=False)` | `DurableInteractionSnapshot` | Load and validate an active or historical journal entry. |
| `load_active(session_id)` | `DurableInteractionSnapshot` | Load the session's currently active interaction. |
| `record_receipt(session_id, *, interaction_id, response, submitted_by="user", expected_revision=None, execution_fence=None)` | `DurableInteractionSnapshot` | Normalize and CAS-persist a response. Identical resubmission is idempotent; conflicting submission fails closed. |
| `require_receipt(session_id, *, interaction_id=None)` | `DurableInteractionSnapshot` | Require an active request with a canonical persisted receipt. |

## DurableMaxBudgetCallbackAdapter

Synchronous adapter used by `KernelLoop` when a memory-backed run also supplies a
callable `on_max_iterations`. It is exported only from
`unchain.interaction.adapters` and is not an application-facing interruption API.

`before_wait()` builds and persists the max-budget request plus continuation,
releases the execution lease, and emits `interaction_requested`. `invoke()` calls
the configured callback, persists its normalized response as a receipt, updates
the session revision, and reacquires the lease before returning the response.

Its `interaction_request` and `wait_revision` fields are populated by
`before_wait()` and are not constructor arguments. Without the configured
callback path, the run returns the ordinary `max_iterations` result rather than a
non-blocking max-budget request.

## Related package exports

`unchain.interaction.__all__` also exposes the following established interaction
primitives. They are listed here so the package export surface remains explicit;
their behavior is described in the linked API or skills chapters.

### Related class exports

| Name | Source | Reference |
| --- | --- | --- |
| `HumanInputOption` | `src/unchain/input/human_input.py:94` | [Input API](input-workspace-schemas.md#humaninputoption) |
| `HumanInputRequest` | `src/unchain/input/human_input.py:122` | [Input API](input-workspace-schemas.md#humaninputrequest) |
| `HumanInputResponse` | `src/unchain/input/human_input.py:258` | [Input API](input-workspace-schemas.md#humaninputresponse) |
| `HumanInputResumePlan` | `src/unchain/interaction/resume.py:27` | Legacy human-input continuation planning. |
| `HumanInputResumeHarness` | `src/unchain/interaction/resume.py:191` | Legacy human-input `on_resume` harness. |
| `FyiMessage` | `src/unchain/interaction/fyi.py:14` | Mid-run FYI message value. |
| `FyiChannel` | `src/unchain/interaction/fyi.py:20` | Thread-safe mid-run FYI channel. |
| `FyiInjectionHarness` | `src/unchain/interaction/fyi.py:76` | Injects queued FYI messages at `before_model`. |
| `ProgressDigest` | `src/unchain/interaction/btw.py:18` | Bounded callback digest for side questions. |
| `QueuedTurnBuffer` | `src/unchain/interaction/queue_turns.py:31` | Thread-safe follow-up buffer drained after a run. |

### Constants

| Name | Source | Purpose |
| --- | --- | --- |
| `ASK_USER_QUESTION_TOOL_NAME` | `src/unchain/input/human_input.py` | Canonical human-input tool name. |
| `HUMAN_INPUT_KIND_SELECTOR` | `src/unchain/input/human_input.py` | Selector request kind. |
| `HUMAN_INPUT_OTHER_VALUE` | `src/unchain/input/human_input.py` | Canonical free-form option value. |
| `INTERACTION_EFFECT_CREATED_BY` | `src/unchain/interaction/effects.py` | Creator identity for durable interaction deltas. |
| `INTERACTION_JOURNAL_KEY` | `src/unchain/interaction/durable.py` | Session document key for the interaction journal. |
| `INTERACTION_KIND_HUMAN_INPUT` | `src/unchain/interaction/durable.py` | `human_input` kind constant. |
| `INTERACTION_KIND_TOOL_APPROVAL` | `src/unchain/interaction/durable.py` | `tool_approval` kind constant. |
| `INTERACTION_KIND_MAX_BUDGET` | `src/unchain/interaction/durable.py` | `max_budget` kind constant. |

### Builder and helper exports

| Name | Source | Role |
| --- | --- | --- |
| `build_ask_user_question_tool` | `src/unchain/input/human_input.py` | Build the canonical human-input tool. |
| `is_human_input_tool_name` | `src/unchain/input/human_input.py` | Test for the canonical human-input tool name. |
| `build_interaction_request` | `src/unchain/interaction/durable.py` | Build a validated immutable request. |
| `build_interaction_receipt` | `src/unchain/interaction/durable.py` | Build a validated receipt bound to a request. |
| `build_human_input_continuation` | `src/unchain/interaction/effects.py` | Build a human-input continuation payload. |
| `build_human_input_requested_event` | `src/unchain/interaction/effects.py` | Build the legacy human-input event. |
| `build_human_input_suspend_request` | `src/unchain/interaction/effects.py` | Build the human-input suspend operation. |
| `build_tool_approval_continuation` | `src/unchain/interaction/effects.py` | Build a tool-approval continuation payload. |
| `build_tool_approval_suspend_request` | `src/unchain/interaction/effects.py` | Build the tool-approval suspend operation. |
| `build_max_budget_continuation` | `src/unchain/interaction/effects.py` | Build a max-budget continuation payload. |
| `build_max_budget_suspend_request` | `src/unchain/interaction/effects.py` | Build the max-budget suspend operation. |
| `parse_human_input_request` | `src/unchain/interaction/resume.py` | Parse a human-input tool call. |
| `prepare_human_input_resume_plan` | `src/unchain/interaction/resume.py` | Validate and prepare a legacy resume plan. |
| `hydrate_human_input_resume_state` | `src/unchain/interaction/resume.py` | Restore state from that plan. |
| `wrap_fyi` | `src/unchain/interaction/fyi.py` | Project an FYI message into model context. |
| `build_btw_prompt` | `src/unchain/interaction/btw.py` | Build a side-question prompt from progress. |
| `merge_queued_turn_texts` | `src/unchain/interaction/queue_turns.py` | Merge queued follow-ups in order. |
