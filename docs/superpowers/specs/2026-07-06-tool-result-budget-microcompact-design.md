# Tool Result Budget Control and Mid-Run Microcompact Design

Date: 2026-07-06
Status: Draft for review

## Summary

Unchain already has tool exposure optimization and deferred tool history compaction, but large tool results can still be appended directly into the active run transcript and the next model input. This design adds two conservative token-saving layers:

1. A global `ToolResultBudgetController` that applies to every tool result by default before it is appended to the transcript or sent to the next model turn.
2. A `MidRunMicrocompactHarness` that compactly rewrites older tool results inside the same agent run only when context pressure is high.

The first layer is always-on guardrail behavior. The second layer is a pressure-triggered cleanup path. Neither layer changes tool selection or deferred tool schema exposure.

## Goals

- Apply budget control to all tool results, including built-in tools, plugin-produced tool results, and third-party tools.
- Keep the first implementation conservative and deterministic.
- Preserve provider protocol invariants, especially assistant tool-call and tool-result pairing.
- Reuse existing per-tool history optimizers where they already exist.
- Keep replacement decisions stable for prompt-cache friendliness.
- Add trace metadata so savings and compaction decisions are visible in run results and callbacks.
- Avoid LLM summarization in the first implementation.
- Avoid adding new `RunState` fields in the first implementation.

## Non-Goals

- Do not change `ToolExposureRuntime`, selector behavior, `tool_search`, or deferred tool schema exposure in this phase.
- Do not add model-generated summaries for compacted tool results.
- Do not delete assistant tool-call messages or reorder provider messages.
- Do not require every tool to implement its own budget logic.
- Do not rely on provider-specific cache-edit APIs.

## Current Behavior

`ToolExecutionHarness._after_tool_batch()` receives accumulated `ToolBatchState.result_messages`, optionally injects observation text, then appends the result messages to both transcript state and the next model input.

For OpenAI previous-response chaining, the next model input is usually just the current tool result messages. For non-chain providers or OpenAI runs without previous-response chaining, `next_model_input` can contain accumulated run context plus result messages.

Existing memory deferred tool compaction runs during memory preparation and targets older history. It does not reliably reduce the current run's immediately-following model request.

## Proposed Architecture

```text
model turn
  -> pending tool calls
  -> ToolExecutionHarness executes tools
  -> ToolResultBudgetController budgets current batch results
  -> ToolExecutionHarness appends budgeted results
  -> MidRunMicrocompactHarness optionally compacts older result content
  -> next model turn
```

The budget controller should be called inside `ToolExecutionHarness._after_tool_batch()` before messages are appended. The microcompact harness should run later in the same `after_tool_batch` phase with a higher order than `tool_execution`, so it can see the appended transcript and next model input.

## Component: ToolResultBudgetController

Suggested module:

```text
src/unchain/tools/result_budget.py
```

Responsibilities:

- Normalize and inspect provider tool-result messages without breaking provider-specific fields.
- Extract visible result payloads where possible.
- Apply a per-tool semantic optimizer when available.
- Apply a generic fallback when no semantic optimizer exists or the optimizer fails.
- Enforce per-result and per-batch budgets.
- Preserve stable metadata about original size, digest, and truncation.
- Return transformed result messages plus budget statistics.

Suggested public types:

```python
@dataclass
class ToolResultBudgetConfig:
    enabled: bool = True
    max_result_chars: int = 50_000
    max_batch_chars: int = 200_000
    preview_chars: int = 4_000
    head_chars: int = 2_000
    tail_chars: int = 2_000
    hash_payloads: bool = True
    preserve_error_results: bool = True
    min_chars_to_budget: int = 8_000

@dataclass
class ToolResultBudgetStats:
    result_count: int
    compacted_count: int
    original_chars: int
    budgeted_chars: int
    saved_chars: int
```

The exact names can change during implementation, but the boundary should stay stable: the caller passes provider, toolkit, tool calls, and result messages; the controller returns budgeted messages and stats.

## Budgeting Algorithm

For each tool result:

1. Identify `tool_call_id` and map it back to the tool name when possible.
2. Estimate visible payload size.
3. Skip compaction if the result is below `min_chars_to_budget`.
4. If the tool has a `history_result_optimizer`, call it with a budget context.
5. If no optimizer exists, use a generic deterministic fallback:
   - strings: head plus tail with omitted character count;
   - lists: count plus compact previews of first and last entries;
   - dicts: preserve shallow keys, compact oversized string/list/dict values;
   - unknown payloads: stable string preview.
6. If the batch still exceeds `max_batch_chars`, apply an additional fair-share pass over the largest results.
7. Attach metadata:
   - `compact: true`
   - `reason: "tool_result_budget"`
   - `original_chars`
   - `original_sha256` when enabled
   - `preview`

The controller should not modify small results. It should not hide failures unless the failure payload itself is extremely large, and even then it should preserve error type, message head, message tail, and digest.

## Component: MidRunMicrocompactHarness

Suggested module:

```text
src/unchain/kernel/microcompact.py
```

Responsibilities:

- Run after `ToolExecutionHarness` in the `after_tool_batch` phase.
- Estimate whether the next model input is near the context limit.
- Rewrite older tool result payloads using the same deterministic compaction machinery.
- Keep recent tool turns intact.
- Update both `state.transcript` and `state.next_model_input` when both are present.
- Emit trace metadata and callback events for visibility.

Suggested config:

```python
@dataclass
class MidRunMicrocompactConfig:
    enabled: bool = True
    trigger_context_ratio: float = 0.85
    trigger_remaining_tokens: int = 12_000
    keep_recent_completed_turns: int = 1
    compact_current_batch: bool = False
    min_savings_chars: int = 8_000
    max_compacted_result_chars: int = 1_200
    preview_chars: int = 300
```

## Microcompact Trigger

The first implementation should support two conservative triggers:

1. Token pressure trigger:
   - active when `state.provider_state.max_context_window_tokens > 0`;
   - estimate next request size from message text length and last known model usage;
   - trigger when estimated ratio exceeds `trigger_context_ratio` or remaining tokens are below `trigger_remaining_tokens`.
2. Large-history trigger:
   - active even without a context window;
   - trigger only when older compactable tool results can save at least `min_savings_chars`.

If the estimate is uncertain, prefer not compacting unless the char-based savings are obvious.

## Microcompact Scope

Microcompact should operate at completed tool-turn boundaries:

```text
assistant message with tool_calls
tool result message
tool result message
assistant message
```

It should keep the newest `keep_recent_completed_turns` tool turns unmodified. With `compact_current_batch=False`, the just-executed batch is always preserved after normal budget control.

Only tool result payload content should be rewritten. The harness must keep:

- message order;
- provider role/type fields;
- `tool_call_id`;
- tool name metadata where present;
- error/success status;
- artifact references;
- digest metadata.

## Cache-Control Implications

Budget control and microcompact should improve cache behavior by making large-result replacements deterministic. A replacement decision should be stable for a given `tool_call_id`, tool name, and original payload digest.

The first implementation does not change provider cache-control placement. It should add metrics that make later cache-policy work easier:

- number of budgeted results;
- number of microcompacted results;
- original and final char estimates;
- whether OpenAI previous-response chaining was active;
- cache read and creation token counts already reported by the provider.

These metrics should use existing trace metadata, `state.metadata`, or `state.optimizer_state`.
The first implementation should not add fields to `RunState`.

## Error Handling

- If a per-tool optimizer raises, fall back to generic compaction and record the optimizer error in trace metadata, not in the visible tool result unless needed.
- If message normalization fails for a provider-specific shape, leave that message unchanged and record a skipped count.
- If microcompact cannot safely identify completed tool turns, it should no-op.
- If applying microcompact would save less than `min_savings_chars`, it should no-op.

## Testing Plan

Add focused tests for:

- current batch result over per-result budget;
- multiple results over per-batch budget;
- per-tool optimizer is preferred over generic fallback;
- optimizer failure falls back safely;
- error result preservation;
- OpenAI previous-response-chain next input remains protocol-valid;
- non-chain provider next input and transcript are both budgeted;
- microcompact skips recent completed tool turns;
- microcompact rewrites older tool results without deleting tool-call pairs;
- microcompact no-ops when context pressure is low;
- trace stats report saved chars and compacted counts.

Existing tests around tool optimizer and memory deferred compaction should remain valid because selector behavior and memory preparation behavior are not in scope.

## Rollout

1. Implement `ToolResultBudgetController` with default conservative limits.
2. Wire it into `ToolExecutionHarness._after_tool_batch()`.
3. Add stats to trace and state metadata.
4. Implement `MidRunMicrocompactHarness` behind default-enabled conservative triggers.
5. Register the harness in the same path that registers default runtime harnesses.
6. Add tests for protocol safety and budget behavior.
7. Run GitNexus `detect_changes()` before committing implementation changes.

## Expected Impact

Primary touchpoints:

- `ToolExecutionHarness._after_tool_batch()` for current batch budget control.
- `KernelLoop` or default agent builder harness registration for installing the microcompact harness.
- Existing `RunState.metadata` or `optimizer_state` buckets for budget/microcompact statistics.
- Existing tool `history_result_optimizer` hooks for semantic compaction reuse.

Risk is expected to be medium because tool-result appending is on the main agent loop path. The design avoids high-risk changes by not altering tool selection, provider request construction, or memory commit semantics in the first phase.
