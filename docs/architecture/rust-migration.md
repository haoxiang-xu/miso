# Incremental Rust kernel migration

The migration is a strangler transition, not a rewrite. Python remains installable, testable, and operational at every phase—including after the Rust kernel becomes the default—while Rust takes ownership only behind versioned contracts and reversible feature gates.

## Target shape

```text
PuPu / Electron             Python SDK and CLI
       |                           |
       +-------- host client ------+
                       |
               unchain.host v1
              (stdio child process)
                       |
                 unchain-core
                    (Rust)
                       |
          providers / tools / storage
```

Electron can spawn and supervise `unchain-core` directly with stdin/stdout pipes. It does not need an HTTP port for the native path. Python uses the same client contract and keeps a local implementation as the compatibility and rollback path.

## Non-negotiable invariants

1. `pip install unchain` and the public Python API continue to work.
2. Every migrated capability has contract fixtures and differential tests against Python before defaulting to Rust.
3. Durable jobs are fenced by claim generation, not process ID alone; stale workers cannot publish a terminal state for a replacement attempt.
4. Rust crashes, incompatible versions, and startup failures produce explicit diagnostics and a deterministic Python fallback where the operation is safe to replay.
5. No native host listens on a network port by default.
6. Rollback is a configuration change, not a data migration.

## Delivery phases and gates

### Phase 0 — reliability and release baseline

- Fence durable worker state and heartbeat writes with an attempt identity.
- Keep one authoritative Python package version and validate wheel purity.
- Run Python tests on Windows, macOS, and Linux.

Exit gate: stale-attempt regression tests pass, the Python suite remains green, and a `py3-none-any` wheel imports in a clean environment.

### Phase 1 — native host foundation

- Establish a pinned Rust workspace with `unchain-protocol` and `unchain-core`.
- Implement bounded JSONL framing, version negotiation, capability discovery, structured errors, build identity, and startup self-test.
- Freeze golden v1 frames before exposing execution methods.

Exit gate: formatting, clippy with warnings denied, unit tests, protocol fixtures, and stdio end-to-end tests pass on all three desktop operating systems.

### Phase 2 — dual-engine Python bridge

- Add a Python `HostClient` with explicit startup, deadline, cancellation, EOF, and stderr-tail behavior.
- Introduce `UNCHAIN_ENGINE=python|rust|auto`; keep `python` as the default initially.
- Add differential tests that run identical requests through both engines and normalize nondeterministic fields.

Exit gate: opt-in Rust runs are observable and safely fall back before execution begins; no mid-operation fallback can duplicate side effects.

### Phase 3 — migrate pure computation

- Move deterministic, low-I/O work first: protocol validation, state transitions, scheduling decisions, token/count utilities, and context transforms.
- Keep provider SDKs and extension loading in Python until the FFI/IPC boundary is proven under load.

Exit gate: benchmarks show a meaningful latency, memory, or reliability win and differential suites show semantic parity.

### Phase 4 — migrate execution ownership

- Move job supervision, cancellation, timeouts, event sequencing, and recovery to Rust one capability at a time.
- Define idempotency and replay rules before each side-effecting method.
- Use an expand/contract schema window: Python and Rust both read and write the shared current format, both read the previous format, and neither emits a new required field until the other implementation can consume it.

Exit gate: soak, crash-recovery, upgrade/downgrade, stale-worker, and "Rust writes → Python rollback reads" tests pass; Rust becomes the default only after a release canary.

### Phase 5 — PuPu native integration

- Package the correct signed `unchain-core` binary per Electron target.
- Spawn it through Electron's main process with pipes, lifecycle ownership, resource limits, and stderr diagnostics.
- Keep the existing sidecar/port path during rollout, then retire it only after parity and rollback drills.

Exit gate: packaged Windows, macOS, and Linux apps pass install, update, suspend/resume, crash recovery, and offline startup tests.

## Repository boundary

The Rust crates live in this repository because they implement Unchain's kernel and share its protocol fixtures, tests, version policy, and releases. PuPu consumes released artifacts; it should not own a fork of the kernel. A future repository split is justified only if the native host develops an independent release cadence or multiple products require governance outside Unchain.

## Refactoring rule

Refactor only to expose a tested execution boundary. Prefer extracting pure contracts and adapters over translating files line by line. A Python component is retired only after its Rust counterpart has parity evidence, production telemetry, and a rehearsed rollback.
