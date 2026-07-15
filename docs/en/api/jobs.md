# Durable Jobs API Reference

The `unchain.jobs` package provides the D4.1 host-local durability layer for
background processes. A detached worker owns the real child process while the
agent runtime stores only durable job state, logs, and cursors. Reconstructing a
toolkit or `ProcessJobSupervisor` therefore does not lose a running job.

## Guarantees and boundary

- `(execution_id, idempotency_key)` produces one stable `job_...` identifier.
- The immutable launch spec binds the store identity, process arguments,
  timeout, and environment-profile digest to that identifier. Reusing the key
  with a different intent fails with `DurableJobConflictError`.
- Each file store has a random persistent `store_id` in `store.json`. Every job
  spec carries that id, and every operation checks that the open store still
  has the same manifest. Deleting and recreating the same path creates a new
  logical store. Job data is physically generation-scoped under
  `stores/<store_id>/jobs`, so an already in-flight stale generation can at most
  recreate an orphaned old namespace; it cannot write into the current one.
- A cross-process claim allows only one detached worker to launch the user
  command, even when multiple runtimes race to start the same job.
- Job lookup, logs, and cancellation are scoped to `execution_id`. A foreign
  execution receives not-found semantics.
- A `ProcessJobSupervisor` captures one immutable `JobEnvironmentProfile` at
  construction. Its exact normalized mapping is passed to both the detached
  worker and the user child; only its SHA-256 digest is written to durable
  state. Ambient environment changes after construction do not alter it.
- `close()` detaches the supervisor. Only `cancel()` requests termination.
- A stale worker lease is not terminalized on its first observation. The
  supervisor first records a process-local suspicion and keeps the job
  `queued`, `starting`, or `running`. Only unchanged lease evidence for the
  full monotonic `suspect_grace_ms` window can be compare-and-set to the durable
  terminal state `outcome_unknown`.
- `outcome_unknown` is monotonic: the supervisor never fabricates success or
  retries the command. It is an orchestration result, not proof that an
  orphaned external process has stopped.
- stdout/stderr, terminal state, and UTF-8 byte cursors survive a fresh runtime.
- Durable tool approval binds the Jobs handler, persistent `store_id`, resolved
  store path, normalized shell intent, and environment-profile digest. Removing
  Jobs, replacing or moving/copying the store, changing the resolved cwd, or
  reconstructing a different environment profile during cold resume fails
  closed before the approved command runs.

This adapter is durable across process restarts on one host and one filesystem.
It is not a distributed scheduler or a claim of exactly-once execution across
machines. D4.1 `wait()` is still a bounded blocking call. Persisted
`WAITING_JOB`, worker release, automatic wakeup, and tool-journal delivery are
the D4.2 boundary.

The environment digest binds strings, not the contents of executables or files
referenced by those strings. D4.1 does not snapshot the filesystem, cwd
contents, PATH targets, container image, or host. Use an external immutable
execution environment when those identities must also be attested.

Terminal job directories are not garbage collected automatically;
applications must budget the configured per-stream log cap and provide their
own retention policy. Orphaned store-generation directories are not collected
automatically either. Process completion follows the launched shell leader.
Detached descendants that outlive that leader are not managed after the job
becomes terminal.

## Store identity

`JsonFileJobStore(base_dir)` resolves `base_dir` to an absolute path and creates
or validates the root `store.json` manifest. Concurrent first-open calls share
one locked manifest creation. The manifest contains
`STORE_MANIFEST_SCHEMA_VERSION`, a random `store_id`, and its creation time. It
selects the current physical namespace at
`base_dir/stores/<store_id>/jobs/...`.

The identity rules are deliberately fail-closed:

- reopening the same intact directory preserves `store_id`;
- deleting and recreating that path produces a new `store_id`;
- a previously opened store object rejects new operations if the manifest has
  been replaced underneath it;
- a stale critical section that passed its identity check before an
  unlink/recreate race remains pinned to its old generation path. Because an
  unlinked lock file and a replacement lock file are different inodes, they may
  no longer coordinate, but the stale write can only create an orphan under the
  old `store_id`; it cannot mutate the current generation;
- a missing manifest while job data exists is corruption, not a request to
  invent a replacement identity;
- every immutable job spec contains `store_id` and rejects data copied from a
  different logical store.

A full directory copy preserves the logical `store_id`, but durable approval
also binds the resolved base path. An approval suspended against the original
path therefore cannot be resumed against the copy. Back up or migrate the
manifest and its selected generation together; do not regenerate `store.json`
independently. Remove orphan generations only after ensuring no stale worker can
still write to them.

## Environment profile

`JobEnvironmentProfile.capture(environment=None)` validates and freezes a
string-to-string environment mapping. When `environment` is omitted, it
captures `os.environ` at supervisor construction. `PYTHONPATH` entries are
resolved to absolute paths and the source root for the running Unchain package
is placed first so the detached worker loads the same code. The resulting exact
mapping is inherited by both worker and child.

The profile entries are excluded from `repr` and are never written to the job
store or durable approval journal. `environment_digest` is the only persisted
form and is exposed on `DurableJobSnapshot` and `DurableJobHandle`. Applications
must still treat the live profile object as secret-bearing memory.

The persisted digest is an unsalted, deterministic SHA-256 value intended for
integrity binding and environment-drift detection, not secret confidentiality.
An attacker who can read the store may be able to test guesses for low-entropy
environment values offline. High-sensitivity deployments should use high-entropy
tokens and an external secret provider; a future profile-id/secret-provider
integration can preserve this binding without treating the digest as a secret
vault.

One supervisor uses one frozen profile for all jobs. If a fresh supervisor
encounters an unclaimed `queued` job whose stored digest differs, it raises
`DurableJobConflictError` for that caller before launching any wrapper or user
command. It does not terminalize or otherwise mutate the shared job: the job
remains `queued`, so a supervisor with the matching profile can still recover
it. The detached wrapper independently rechecks the inherited profile digest
before acquiring its claim. For an approval suspended before a job was reserved,
profile drift is detected earlier as an interaction integrity failure.

## Lease reconciliation and `outcome_unknown`

The worker starts an independent heartbeat lease immediately after acquiring
its launch claim. That heartbeat continues through command teardown, log
drain/fsync, and the durable terminal-state write. This prevents normal
finalization work from looking like a dead worker.

For missing or stale heartbeat evidence after the launch grace period,
reconciliation is two-stage:

1. The first observation retains the current public status and records a local
   suspicion signature containing state revision, worker identity, heartbeat
   sequence/timestamp, and the claim generation where applicable.
2. A later reconciliation may write `outcome_unknown` only if that signature has
   remained unchanged for `suspect_grace_ms`. The final store transition again
   compares state revision/status and heartbeat sequence under the job lock; a
   queued pre-launch suspicion also compares claim id. Any observed progress
   resets the grace.

Suspicion is supervisor-local and observation-driven. It advances when
`inspect()`, `poll()`, `wait()`, `cancel()`, or `reattach()` reconciles the job;
there is no independent timer that terminalizes it in the background, and a
fresh supervisor starts a fresh suspicion window. Structurally invalid claim
evidence is an integrity failure and does not receive the stale-lease grace.

`outcome_unknown` sets `completed=True` for orchestration, but logs are not
treated as final because an external process may still be writing. Later polls
may therefore return more bytes. Since it is already terminal, `cancel()` does
not issue a new cancellation marker; operator or adapter-specific reconciliation
is required.

## Log cursor semantics

`poll()`, `wait()`, and `cancel()` all consume the same persisted per-job log
cursor. `inspect()` and `reattach()` do not consume logs. stdout and stderr have
independent UTF-8 byte offsets, but they are updated atomically under the same
job lock. Concurrent consumers divide one stream rather than each receiving a
copy.

The public result fields mean:

| Field | Meaning |
| --- | --- |
| `stdout_offset`, `stderr_offset` | Byte offset at which this result started reading. |
| `next_stdout_offset`, `next_stderr_offset` | Next byte offset already persisted for the shared consumer. |
| `stdout_available`, `stderr_available` | Stream size observed while producing the result. |
| `offset_unit` | Always `"utf8_bytes"`. |

`max_output_chars` is applied independently to each stream. A trailing partial
UTF-8 code point is held for a later read; a proven-final stream may flush an
incomplete final sequence as the Unicode replacement character.

The cursor is durably advanced before the result is returned to the caller and,
for an Agent tool call, before that result is committed to the transcript. A
crash in this gap can therefore prevent those bytes from reaching the model.
This is durable log storage, not crash-idempotent result delivery. `truncated`
can mean either that more bytes remain for a later cursor read or that the
producer hit its configured log cap; `stdout_truncated` and `stderr_truncated`
identify the latter permanent capture limit.

## Public surface

| Name | Role |
| --- | --- |
| `JsonFileJobStore` | Atomic local job specs, states, claims, heartbeats, cancellation markers, logs, cursors, persistent `store_id`, and generation-isolated job paths. |
| `ProcessJobSupervisor` | `start`, `inspect`, `poll`, `wait`, `cancel`, and `reattach` orchestration. |
| `JobEnvironmentProfile` | Constructor-frozen normalized environment; entries stay in memory and only its digest is persisted. |
| `DurableJobHandle` | Stable external job identity. |
| `DurableJobSnapshot` | Immutable durable state view. |
| `DurableJobResult` | JSON-friendly poll/wait/cancel result with attribute access. |
| `DurableShellJobPlugin` | Routes background `CoreToolkit.shell` calls to the supervisor. |
| `JobsModule` | Registers the shell plugin on an `AgentBuilder`. Exported from `unchain.agent`. |

The package also exports `DurableJobError` and the typed not-found, ownership,
conflict, and corruption subclasses, along with job, environment-profile, and
store-manifest schema constants.

## ProcessJobSupervisor

```python
ProcessJobSupervisor(
    store,
    *,
    python_executable=None,
    heartbeat_stale_ms=None,
    launch_grace_ms=None,
    poll_interval_s=None,
    default_max_log_bytes=50 * 1024 * 1024,
    poll_interval_ms=None,
    heartbeat_interval_ms=None,
    heartbeat_stale_after_ms=None,
    cancel_grace_ms=1000,
    startup_timeout_ms=None,
    suspect_grace_ms=None,
    monotonic_clock=None,
    environment=None,
)
```

The resolved defaults are 5,000 ms for stale heartbeat detection, 2,000 ms for
launch grace, 50 ms for wait-loop polling, and one stale-heartbeat window for
the suspicion grace. `heartbeat_stale_after_ms`, `startup_timeout_ms`, and
`poll_interval_ms` are compatibility aliases for their corresponding primary
parameters and cannot be supplied together with them. `environment` accepts a
mapping or an existing `JobEnvironmentProfile` and is captured once by the
constructor; individual `start()` calls cannot substitute another profile.

Main methods:

- `start(..., execution_id, idempotency_key, argv, cwd, timeout_ms)` reserves
  the stable identity and launches an independent worker. `intent_digest` is
  optional; when omitted, the supervisor derives one from the process intent
  including its frozen environment digest.
- `inspect(job_id, execution_id=...)` returns a `DurableJobSnapshot` and
  reconciles worker liveness. It is not a pure read: it may safely recover an
  unclaimed queued launch or advance lease suspicion. A queued environment
  mismatch raises `DurableJobConflictError` without mutating the shared job.
- `poll(...)` consumes the next persisted log increment and advances the shared
  cursor.
- `wait(..., timeout_ms=...)` waits only for the supplied bound; it does not
  convert the execution into a durable sleeping checkpoint. It consumes one
  log increment before returning.
- `cancel(...)` first persists a cancellation marker, then waits for the owning
  worker to record a terminal receipt. The supervisor never signals an
  unverified PID. Its result also consumes one log increment.
- `reattach(execution_id)` reconciles jobs owned by that execution and recovers
  queued launches whose environment digest matches the supervisor. A mismatch
  fails that caller with `DurableJobConflictError` and leaves the job queued for
  a matching supervisor.
- `close()` releases only the caller's supervisor object.

## Agent shell integration

```python
from pathlib import Path

from unchain import Agent
from unchain.agent import JobsModule, ToolsModule
from unchain.jobs import JsonFileJobStore, ProcessJobSupervisor
from unchain.toolkits import CoreToolkit

workspace = Path.cwd()
supervisor = ProcessJobSupervisor(
    JsonFileJobStore(Path.home() / ".unchain" / "job-store" / "my-app")
)

agent = Agent(
    name="durable-worker",
    modules=(
        ToolsModule(tools=(CoreToolkit(workspace_root=workspace),)),
        JobsModule(supervisor=supervisor),
    ),
)
```

`JobsModule` does not replace the shell schema. Foreground calls and legacy
process-local task ids keep their existing path. Only
`run_in_background=True` and lifecycle calls whose `task_id` starts with
`job_` are intercepted. Existing confirmation, denial, and modified-argument
semantics remain in force. A durable background start requires a non-empty
`session_id`; with `JobsModule` installed, omission fails closed instead of
silently creating a process-local task. A shell selected through
`ToolOptimizerModule`'s deferred executor is routed through the same Jobs
handler rather than falling back to the legacy in-memory task runtime.

Use an application-scoped, persistent, private store directory outside
tool-writable workspace roots; `session_id` is the execution identity inside
that store. Logs can contain command output and the immutable spec contains the
command arguments and environment digest. Raw environment entries are retained
only in the live `JobEnvironmentProfile`, not in store JSON. The file store
creates its directories with owner-only permissions on POSIX. A stronger
adversarial boundary requires a separate service or OS identity; a local shell
running as the same user is not a security sandbox.
