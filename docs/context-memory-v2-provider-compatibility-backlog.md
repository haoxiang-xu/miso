# Context Memory V2 Provider Compatibility Backlog

This backlog records non-blocking provider-adapter differences discovered
after the Memory V2 P0 scope was locked. They are not rollout stop-ship issues
unless they later cause functional impossibility, durable corruption or loss,
plaintext secret exposure, or duplicate external side effects.

## Anthropic beta header textual normalization

- Current native adapter preserves duplicate existing beta values, for example
  `anthropic-beta: dup,dup`.
- The provider wire preparer canonicalizes that value to `dup` before appending
  required tool betas.
- Both requests declare the same beta capability set. The difference is textual
  and does not change provider semantics.
- Revisit when the production transport sends the persisted wire envelope
  directly. At that point either make canonical de-duplication the adapter
  contract or preserve the original text in both paths.

## Intentional strict-input differences

- Missing OpenAI function-call IDs are rejected before persistence instead of
  generating a random ID at send time.
- Empty OpenAI previous-response IDs are rejected instead of treated as absent.
- Anthropic empty or non-cacheable message tails are rejected before transport.
- Provider request inputs require strict canonical JSON rather than permissive
  Python objects.

These differences support deterministic durable replay and should remain unless
a concrete provider compatibility failure requires a narrower rule.

## Stream callback delivery

- Durable provider turns buffer request, token, and reasoning events until the
  final result receipt and request-lease completion are durable.
- P0 uses at-most-once delivery for those buffered stream events. A process
  crash after result persistence but before callback release recovers the final
  `ModelTurnResult`, but does not replay partial token or reasoning events.
- Guaranteed stream-event replay would require a separate durable callback
  outbox. That is intentionally outside P0 and must not be folded into provider
  result recovery.

## Provider-native replay seeds

- OpenAI can reconstruct its replay prefix from the persisted route `input`.
- Anthropic-family routes merge system/developer content into the native
  `system` field, so the route cannot reproduce the legacy pre-wire message
  list byte-for-byte. The provider-native replay is semantically equivalent.
- If exact byte parity becomes a requirement, add a hash-bound, non-wire replay
  seed in a future envelope schema rather than deriving it from decorated wire
  tools or cache-control fields.

## Durable tool handler version declarations

- The P0 Toolkit adapter derives a restart-stable handler ID from the tool
  name, toolkit metadata, and callable module/qualified-name manifest.
- It currently assigns handler revision `1`; the tool configuration and full
  provider descriptor remain independently hash-bound, so request drift still
  fails closed.
- A future host manifest may declare explicit handler IDs and revisions for
  closures, dynamically loaded plugins, and intentionally versioned behavior.
  This is rollout hardening, not a reason to reopen the P0 provider boundary.

## Enable exact durable provider-turn recovery in the PuPu active host

- Unchain's `TaskStateContextRuntime.from_factory(...)` keeps
  `provider_turns_enabled` disabled unless the host explicitly opts in.
- The current PuPu active factory does not opt in, so the canonical Context
  Compiler and durable tool authority are active, but the exact provider
  request lease/result-reuse path is not yet mounted in production.
- Durable tool execution still prevents duplicate tool side effects. The
  remaining crash boundary may repeat a model-provider request and its cost;
  it does not justify reopening the locked P0 ownership slice.
- Before canary, enable the Unchain-owned provider-turn runtime deliberately
  and add a symmetric active-host matrix for normal, graph, resume, and
  subagent paths across OpenAI, Anthropic-family, and Ollama-compatible
  providers. Existing coverage is split between compiler/wire parity,
  OpenAI-to-Anthropic graph E2E, and Ollama-focused active-path tests.
