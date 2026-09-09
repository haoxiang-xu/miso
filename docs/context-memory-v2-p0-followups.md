# Context Memory V2 P0 Follow-ups

This document records non-blocking improvements discovered while completing
the locked Memory V2 P0 production path. They must not interrupt P0 unless a
finding later meets one of the stop-ship conditions in
`context-memory-v2-security-backlog.md`.

## Recover a parent tool completion from a durable subagent handoff

- **Current behavior:** A completed child result is copied into the parent
  execution and `handoff.recorded` is durable before the subagent plugin
  returns. The parent tool result and completion seal are persisted
  immediately afterward by the ordinary durable tool executor.
- **Crash boundary:** If the process exits after `handoff.recorded` but before
  the parent tool completion seal, restart classifies the parent tool as
  uncertain-after-start. It fails closed and does not run the child again, so
  external side effects are not duplicated and the complete child output is
  still durable and readable.
- **Deferred improvement:** Add an exact, hash-bound recovery adapter that can
  derive and seal the parent tool completion from the matching handoff receipt
  without invoking the child or any tool handler. This requires a versioned
  binding between the tool execution subject, call ID, child attempt, handoff
  envelope, and reconstructed tool result.
- **P0 disposition:** Non-blocking. Preserve the current fail-closed behavior;
  do not broaden automatic replay.

## Additional bounded-review follow-ups

The one-pass P0 review also recorded these non-blocking integrity improvements.
They are intentionally deferred and must not reopen the P0 review loop:

- Add a digest to the redundant link-provenance projection and verify it when
  loading link rows. The canonical link descriptor remains the authoritative
  checked record today.
- Page failed-source candidate isolation beyond the current bounded 201-item
  batch. The current coordinator already caps one consolidation job at 200;
  this only affects pathological runs that create more candidates before a
  failed or partial completion.
- Revalidate all task-state receipt target fields inside the transactional
  replay check, matching the stricter outer replay validation. Current writes
  remain scoped by binding and CAS revision.

## Serialize first attachment with concurrent chat deletion

- **Current guarantee:** A host factory reads and verifies a durable chat
  tombstone before opening any Context/Memory V2 store. A tombstone that
  already exists therefore rejects attachment with zero new schemas, rows, or
  object-directory writes.
- **Race boundary:** First attachment and deletion are not serialized by one
  owner-scoped transaction. Execution rows do not carry `owner_chat_id`, while
  deletion guard triggers can only recognize execution IDs already captured in
  the tombstone scope. A deletion racing the first bind could therefore pass
  both preflight checks before either side records enough owner evidence.
- **Deferred improvement:** Introduce one owner-scoped admission/deletion lock
  or equivalent transactional ownership claim, and make both first attachment
  and deletion participate in it before creating execution-scoped rows.
- **P0 disposition:** Non-blocking under the locked acceptance contract, which
  covers deleted-before-open behavior. Do not change the schema or locking
  protocol in this slice.

## Repair the stale full-test launcher

- **Current behavior:** The repository's `run_tests.sh` exits before pytest
  because its preflight still imports the former `miso` package name.
- **Verified fallback:** Running the same checked-in virtual environment
  directly with `.venv/bin/python -m pytest -q tests` completes successfully.
- **Deferred improvement:** Update the launcher preflight to the current
  `unchain` package layout and add a small launcher contract test.
- **P0 disposition:** Test-harness only. It does not change production Context
  V2 behavior and must not interrupt the ownership cutover.

## Checkpoint oversized closed human-interaction history

- **Current behavior:** A resolved `ask_user_question` is no longer retained as
  an unfinished or atomic tool call. The compiler instead keeps a bounded,
  provider-neutral response envelope containing the exact interaction/tool
  correlation, response preview, integrity metadata, and durable artifact ref;
  it does not synthesize a `tool_result`. The locked reproduction with 20
  resolved interactions, 1.2 KiB ask arguments, a real 8,192-token window, and
  pressured earlier history compiles successfully and retains all 20 response
  envelopes.
- **Scale boundary:** Closed semantic-history envelopes are currently injected
  as mandatory history rather than covered by a semantic-event checkpoint. A
  synthetic 96-resolved-interaction workload at a 16,384-token window can
  therefore exceed the mandatory budget even though every raw journal event
  and response artifact remains durable and readable.
- **Deferred improvement:** Extend deterministic checkpoint coverage to older,
  closed semantic-event ranges and project a compact checkpoint/ref envelope
  with exact source cursors and integrity bindings. Do not reintroduce Last-N
  truncation or silently discard resolved responses.
- **P0 disposition:** Non-blocking beyond the locked acceptance scale. Keep it
  as backlog; do not expand the current closure fix into a new checkpoint
  architecture.

## Serialize concurrent consumers of one durable tool invocation

- **Current behavior:** The focused concurrent-consumer test is intermittent:
  one of five repeated runs failed while the full suite subsequently passed.
  The observed failure returned one successful consumer instead of the two
  identical successful receipts expected by the test.
- **Existing safety boundary:** The durable invocation still prevents a second
  handler execution; no duplicate external effect was observed.
- **Deferred improvement:** Make receipt publication and waiter wake-up fully
  deterministic for concurrent consumers of the same bound invocation, then
  add a stress loop that distinguishes duplicate execution from delayed
  receipt visibility.
- **P0 disposition:** Non-blocking while handler execution remains exactly once.
  Promote to stop-ship only if a duplicate external effect or durable result
  loss becomes reproducible.

## Completed: roleless module identity and durable grants

- **Current guarantee:** Agent calls carry a generic `ExecutionIdentity` and
  host-issued `ModuleGrant` values. Lineage records execution facts only;
  authority is derived exclusively from explicit capabilities and an optional
  non-delegable authority value.
- **Durability boundary:** Canonical graph/resume snapshots persist the exact
  identity, lineage, capability set, delegable subset, and authority. Legacy
  flat role snapshots are identified explicitly and cannot be resumed as
  canonical bindings.
- **Delegation:** The core subagent runtime delegates arbitrary module grants
  through a generic propagation contract. Each template may request a subset
  of delegable capabilities. Adding a collaboration pattern does not require a
  Memory-specific role or a Memory-module code change.
- **Closed compatibility path:** `MemoryV2RunRole` and its compatibility export
  modules have been removed. Production code must not reconstruct permission
  from root/subagent/graph labels.

## Preserve promotion decision reasons as durable provenance

- **Current behavior:** The official confirmation-gated promotion record binds
  the decision operation, exact proposal revision, confirmation ID, approval
  value, and derived long-term revision. It does not persist the optional
  route-level `decision_reason` text.
- **Current API disposition:** The PuPu official promotion adapter reports
  non-empty decision reasons as unsupported and never claims that they were
  persisted.
- **Deferred improvement:** Version the official promotion decision model and
  SQLite migration so a sanitized decision reason becomes immutable durable
  provenance without weakening CAS, replay, or legacy-record verification.
- **P0 disposition:** Non-blocking. User confirmation remains mandatory and
  durable; do not modify the CRITICAL `PromotionProposal` schema in this slice.

## Add an official folder-metadata update operation

- **Current behavior:** The official workspace service can create folders and
  move them with CAS, but its public update operations do not represent a
  description-only folder metadata change. The PuPu host adapter therefore
  rejects that one request shape instead of writing private SQLite fields.
- **Deferred improvement:** Add a revisioned folder metadata operation to the
  Unchain workspace service and SQLite repository, with the same operation-ID
  replay and expected-space-revision guarantees as the existing move path.
- **P0 disposition:** Non-blocking. Folder creation, listing, traversal, and
  move remain available; do not bypass the official workspace API during the
  ownership cutover.

## Official atomic Memory review decision seam (resolved in P0)

- **Current behavior:** The Unchain-owned review-decision repository now
  applies or rejects an immutable `memory_review_proposals` record in one
  transaction. Apply creates the next workspace revision and transitions the
  candidate/job binding; reject transitions the candidate/job without changing
  the workspace entry. Both paths persist an idempotent, revision-fenced
  decision receipt.
- **Host boundary:** PuPu exposes only the thin scoped adapter and
  `/context/v2/memory/reviews/<review_id>/decision` route. The request remains
  bound to the current chat, space, proposal revision, candidate revision, and
  target-space revision; stale or ambiguous state fails closed.
- **P0 disposition:** Resolved. Conflict review decisions no longer block the
  rollout gate, and the host does not call private SQLite mutation helpers.

## Preserve structured or multimodal content during generation rebase

- **Current behavior:** The renderer's canonical turn-mutation projector sends
  only non-empty string `user` and `assistant` content. It deliberately drops
  attachment-only turns and all attachment metadata before calling rebase.
  The atomic Unchain generation service therefore imports string snapshots
  only and rejects broader JSON content accepted by the Electron transport
  validator.
- **Deferred improvement:** Define a provider-neutral, canonical structured
  message schema, durable attachment refs, redaction rules, and compiler
  projection behavior before widening `GenerationSnapshotMessage.content`.
- **P0 disposition:** Non-blocking for the reachable renderer contract. Reject
  structured content at the PuPu adapter boundary; do not widen model-visible
  history semantics during the ownership cutover.
